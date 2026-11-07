"""Resolución determinista de expresiones de fecha en castellano.

Motivo (spike del 12/10/2026, docs/arquitectura.md DA-08): los modelos de 1,7-4B
en CPU aciertan la hora casi siempre, pero fallan entre el 50 % y el 70 % de las
fechas relativas ("el jueves", "el sábado"), porque eso exige aritmética de
calendario. Se reparte el trabajo así: el modelo extrae la expresión, que es
una tarea de clasificación, y este módulo calcula la fecha, que es código
testeable.

Formas admitidas para un día:
    hoy · mañana · pasado mañana
    lunes … domingo   (con o sin "el", "este", "próximo", "que viene")
    2026-11-03 · 3/11 · 3/11/2026 · 3 de noviembre [de 2026]
Formas admitidas para un periodo:
    cualquier día · esta semana · semana que viene · este mes · mes que viene · fin de semana

Tolerancia (Incremento 1, DEV-01): las palabras clave (días, meses, "mañana",
"semana", "que viene"…) se corrigen con difflib si llegan con una errata
("vierns", "semna"), y "finde" equivale a "fin de semana". Para la hora se
admiten "mediodía" (12:00) y "medianoche" (00:00).
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from datetime import date, datetime, time, timedelta

DIAS_SEMANA = {"lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3,
               "viernes": 4, "sabado": 5, "domingo": 6}
MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
         "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
         "noviembre": 11, "diciembre": 12}
_RELLENO = re.compile(r"\b(el|la|los|las|este|esta|proximo|proxima|que viene|siguiente|dia)\b")

# Vocabulario contra el que se corrigen las erratas. Solo palabras de 4 letras
# o más: en las cortas ("mes", "hoy") una errata deja una palabra demasiado
# distinta para corregirla con seguridad.
_VOCABULARIO = sorted(
    set(DIAS_SEMANA) | set(MESES)
    | {"manana", "pasado", "semana", "viene", "proximo", "proxima", "siguiente"}
)
_VOCABULARIO_HORA = ["mediodia", "medianoche"]
_CORTE_ERRATAS = 0.75


def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFKD", str(texto).lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip()


def _corregir(texto: str, vocabulario: list[str] = _VOCABULARIO) -> str:
    """Sustituye cada palabra con una errata por la palabra clave más parecida.

    Trabaja sobre texto ya normalizado. Las palabras que ya están en el
    vocabulario, las de menos de 4 letras y las que llevan cifras no se tocan.
    """
    def una(m: re.Match) -> str:
        p = m.group(0)
        if len(p) < 4 or p in vocabulario:
            return p
        cerca = difflib.get_close_matches(p, vocabulario, n=1, cutoff=_CORTE_ERRATAS)
        return cerca[0] if cerca else p

    t = re.sub(r"[a-z]+", una, texto)
    return re.sub(r"\bfinde\b", "fin de semana", t)


def _siguiente(hoy: date, dia_semana: int) -> date:
    """Próxima aparición estrictamente posterior a hoy.

    Decisión: dicho un lunes, "el lunes" se entiende como el de la semana que
    viene, que es lo habitual en castellano. "El martes" y "el martes que viene"
    se tratan igual; la ambigüedad real de la segunda forma queda documentada
    en lugar de resolverse con una regla arbitraria.
    """
    delta = (dia_semana - hoy.weekday()) % 7 or 7
    return hoy + timedelta(days=delta)


def resolver_dia(expr: str | None, hoy: date) -> date | None:
    if not expr:
        return None
    t = _corregir(normalizar(expr))

    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:  # "2026-13-45", "2026-02-30": fecha imposible
            return None
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", t)
    if m:
        return _con_anio(int(m[1]), int(m[2]), m[3], hoy)
    m = re.search(r"(\d{1,2}) de (" + "|".join(MESES) + r")(?: de (\d{4}))?", t)
    if m:
        return _con_anio(int(m[1]), MESES[m[2]], m[3], hoy)

    if "pasado manana" in t:
        return hoy + timedelta(days=2)
    if re.search(r"\bmanana\b", t):
        return hoy + timedelta(days=1)
    if re.search(r"\bhoy\b", t):
        return hoy
    for nombre, n in DIAS_SEMANA.items():
        if re.search(rf"\b{nombre}\b", t):
            return _siguiente(hoy, n)
    return None


def resolver_dia_con_reloj(expr: str | None, ahora: datetime) -> date | None:
    """Como resolver_dia, pero a partir del instante actual con zona horaria.

    Lo usa el dominio: el reloj del módulo (hora real o MORTIMER_AHORA) ya
    está en la zona del usuario, así que "hoy" es la fecha local de ese instante.
    """
    if ahora.tzinfo is None or ahora.utcoffset() is None:
        raise ValueError("resolver_dia_con_reloj necesita un datetime con zona horaria")
    return resolver_dia(expr, ahora.date())


def _con_anio(d: int, m: int, anio: str | None, hoy: date) -> date | None:
    try:
        if anio:
            a = int(anio)
            return date(a + 2000 if a < 100 else a, m, d)
        f = date(hoy.year, m, d)
        # Sin año explícito, una fecha ya pasada se entiende del año siguiente.
        return f if f >= hoy else date(hoy.year + 1, m, d)
    except ValueError:
        return None


def resolver_hora(expr: str | None) -> time | None:
    """Acepta 'HH:MM', 'H', '9h', '21.30', 'mediodía' y 'medianoche'. La
    conversión '5 de la tarde' → 17:00 la hace el modelo; en el spike no falló
    ni una vez."""
    if expr is None or expr == "":
        return None
    t = _corregir(normalizar(expr), _VOCABULARIO_HORA)
    # Las palabras van antes que las cifras: "las 12 del mediodía" → 12:00.
    if re.search(r"\bmediodia\b", t):
        return time(12, 0)
    if re.search(r"\bmedianoche\b", t):
        return time(0, 0)
    m = re.search(r"(\d{1,2})(?:[:.h](\d{2}))?", t)
    if not m:
        return None
    h, mi = int(m[1]), int(m[2] or 0)
    return time(h, mi) if h < 24 and mi < 60 else None


def resolver_momento(dia: str | None, hora: str | None, hoy: date) -> datetime | None:
    d = resolver_dia(dia, hoy)
    if d is None:
        return None
    return datetime.combine(d, resolver_hora(hora) or time(0, 0))


def resolver_periodo(expr: str | None, hoy: date) -> tuple[date, date] | None:
    """Devuelve (desde, hasta), ambos inclusive."""
    if not expr:
        return None
    t = _corregir(normalizar(expr))
    lunes = hoy - timedelta(days=hoy.weekday())
    if "fin de semana" in t:
        sab = lunes + timedelta(days=5)
        if "que viene" in t or "proximo" in t:
            sab += timedelta(days=7)
        return sab, sab + timedelta(days=1)
    if "semana" in t:
        if any(x in t for x in ("que viene", "proxima", "siguiente")):
            return lunes + timedelta(days=7), lunes + timedelta(days=13)
        return hoy, lunes + timedelta(days=6)
    if "mes" in t:
        if any(x in t for x in ("que viene", "proximo", "siguiente")):
            ini = (hoy.replace(day=1) + timedelta(days=32)).replace(day=1)
        else:
            ini = hoy
        fin = (ini.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        return ini, fin
    d = resolver_dia(expr, hoy)
    return (d, d) if d else None

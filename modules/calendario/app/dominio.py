"""Lógica de dominio del Calendario.

Un único servicio, `Calendario`, con dos familias de operaciones:

- Las del contrato MCP (crear, listar, cancelar, huecos). Reciben lo que
  extrae el modelo (expresiones como "el viernes" o "17:00"), resuelven las
  fechas con fechas.py y devuelven siempre {"texto", "final", "datos"}: una
  frase para el usuario que el core puede mostrar tal cual (final=True) sin
  otra ronda del LLM. Los errores de interpretación también son finales y
  terminan en una pregunta.
- Las de la API y el frontend (alta_directa, editar, cancelar_por_id,
  restaurar, listar_rango, reset). Trabajan con fechas ISO y lanzan
  ErrorValidacion o NoEncontrado, que main.py traduce a 400 y 404.

Reglas que valen para todo el módulo:
- Cancelar es un borrado lógico (`cancelado_en`) y se puede restaurar.
- Ante una cancelación ambigua no se toca nada: se devuelven los candidatos.
  Tampoco se cancela si el evento solo encaja al revés ("cena de empresa"
  frente a "Cena") o con erratas: se propone y se pregunta (QA A-01).
- Un evento de día completo no ocupa franja: ni provoca avisos de
  solapamiento ni reduce los huecos (como TRANSP:TRANSPARENT en iCalendar).
- El reloj se inyecta (hora real o MORTIMER_AHORA), así que todo es
  reproducible en pruebas y en la evaluación.
"""

from __future__ import annotations

import difflib
import re
import uuid
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any, Callable
from zoneinfo import ZoneInfo

from . import fechas
from .db import BaseDatos

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
NUMEROS = {2: "dos", 3: "tres", 4: "cuatro", 5: "cinco"}
DURACIONES = {15: "un cuarto de hora", 30: "media hora", 45: "tres cuartos de hora",
              60: "una hora", 90: "hora y media", 120: "dos horas"}
# Palabras que no identifican un evento: "cancela la cita del dentista" → "dentista".
_VACIAS = {"el", "la", "los", "las", "lo", "un", "una", "de", "del", "con", "al", "a", "en",
           "mi", "mis", "y", "que", "para", "por", "cita", "evento", "eso"}
MAX_CANDIDATOS = 5
DURACION_POR_DEFECTO = timedelta(hours=1)


class ErrorValidacion(ValueError):
    """Datos de entrada no válidos (la API responde 400)."""


class NoEncontrado(LookupError):
    """El evento no existe (la API responde 404)."""


def resultado(texto: str, datos: dict[str, Any] | None = None, final: bool = True) -> dict[str, Any]:
    return {"texto": texto, "final": final, "datos": datos or {}}


def _hora(t: datetime | time) -> str:
    """Hora para frases: '8:30', '16:00'."""
    return f"{t.hour}:{t.minute:02d}"


def _hora2(t: datetime | time) -> str:
    """Hora para listas y franjas: '08:30'."""
    return f"{t.hour:02d}:{t.minute:02d}"


def _y(partes: list[str]) -> str:
    return partes[0] if len(partes) == 1 else ", ".join(partes[:-1]) + " y " + partes[-1]


def _duracion(minutos: int) -> str:
    return DURACIONES.get(minutos, f"{minutos} minutos")


def _palabras(texto: str) -> list[str]:
    return [p for p in re.findall(r"[a-z0-9]+", fechas.normalizar(texto))
            if len(p) >= 3 and p not in _VACIAS]


_VERDADEROS = {"1", "true", "on", "si", "yes", "verdadero", "t", "y", "s"}


def a_bool(valor: Any) -> bool:
    """Booleano tolerante para JSON y formularios: "false", "0", "off" y "" son falsos.

    bool("false") es True en Python; aquí solo cuentan como verdaderos True,
    los números distintos de 0 y los textos true/1/on/sí/yes.
    """
    if isinstance(valor, bool):
        return valor
    if valor is None:
        return False
    if isinstance(valor, (int, float)):
        return valor != 0
    return fechas.normalizar(str(valor)) in _VERDADEROS


_NUM_PALABRAS = {"un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
                 "siete": 7, "ocho": 8}


def minutos_desde_texto(valor: Any) -> int | None:
    """Duración en minutos a partir de lo que mande el modelo: 90, "90", "90 minutos",
    "una hora", "hora y media", "1,5 horas", "2h", "media hora", "tres cuartos de hora".
    Devuelve None si no se entiende (o no es positiva)."""
    if isinstance(valor, bool) or valor is None:
        return None
    if isinstance(valor, (int, float)):
        return int(valor) if valor > 0 else None
    t = fechas.normalizar(str(valor))
    if not t:
        return None
    if re.fullmatch(r"\d+", t):
        n = int(t)
        return n if n > 0 else None
    m = re.fullmatch(r"(\d+)\s*(?:m|min|mins|minuto|minutos)\.?", t)
    if m:
        return int(m[1]) or None
    total = 0.0
    m = re.match(r"(\d+(?:[.,]\d+)?)\s*(?:h|hora|horas)\b", t)
    if m:
        total = float(m[1].replace(",", ".")) * 60
        resto = t[m.end():]
    else:
        m = re.match(r"(?:(\w+)\s+)?(cuartos?) de hora\b", t)
        if m:
            n = _NUM_PALABRAS.get(m[1] or "un")
            if n is None:
                return None
            return n * 15
        m = re.match(r"media hora\b", t)
        if m:
            return 30
        m = re.match(r"(?:(\w+)\s+)?horas?\b", t)
        if not m:
            return None
        n = _NUM_PALABRAS.get(m[1]) if m[1] else 1
        if n is None:
            return None
        total = n * 60.0
        resto = t[m.end():]
    resto = resto.strip()
    extra = {"": 0, "y media": 30, "y cuarto": 15, "y tres cuartos": 45}.get(resto)
    if extra is None:
        m = re.fullmatch(r"y (\d+)\s*(?:m|min|minutos?)?", resto)
        if not m:
            return None
        extra = int(m[1])
    total += extra
    return int(round(total)) if total > 0 else None


class Calendario:
    def __init__(self, db: BaseDatos, zona: str | tzinfo = "Europe/Madrid",
                 reloj: Callable[[], datetime] | None = None,
                 huecos_desde: str | time = "08:00", huecos_hasta: str | time = "21:00"):
        self.db = db
        self.zona = ZoneInfo(zona) if isinstance(zona, str) else zona
        self._reloj = reloj or (lambda: datetime.now(self.zona))
        self.huecos_desde = huecos_desde if isinstance(huecos_desde, time) else fechas.resolver_hora(huecos_desde)
        self.huecos_hasta = huecos_hasta if isinstance(huecos_hasta, time) else fechas.resolver_hora(huecos_hasta)
        if self.huecos_desde is None or self.huecos_hasta is None or self.huecos_desde >= self.huecos_hasta:
            raise ValueError("Franja de búsqueda de huecos no válida")

    # ── Utilidades de tiempo ────────────────────────────────────────────────

    def ahora(self) -> datetime:
        a = self._reloj()
        if a.tzinfo is None:
            a = a.replace(tzinfo=self.zona)
        return a.astimezone(self.zona)

    def _local(self, d: date, t: time = time(0, 0)) -> datetime:
        return datetime.combine(d, t, tzinfo=self.zona)

    @staticmethod
    def _iso(dt: datetime) -> str:
        return dt.isoformat(timespec="seconds")

    def _parsear(self, valor: Any) -> datetime | date:
        """ISO 8601 → datetime en la zona del módulo, o date si solo hay fecha."""
        if isinstance(valor, datetime):
            dt = valor
        elif isinstance(valor, date):
            return valor
        else:
            texto = str(valor or "").strip()
            if not texto:
                raise ErrorValidacion("Falta la fecha")
            if texto.endswith("Z"):
                texto = texto[:-1] + "+00:00"
            try:
                if len(texto) == 10:
                    return date.fromisoformat(texto)
                dt = datetime.fromisoformat(texto)
            except ValueError:
                raise ErrorValidacion(f"Fecha no válida: '{valor}'") from None
        return dt.replace(tzinfo=self.zona) if dt.tzinfo is None else dt.astimezone(self.zona)

    def _inicio(self, ev: dict[str, Any]) -> datetime:
        return self._parsear(ev["inicio"]) if len(ev["inicio"]) > 10 else self._local(self._parsear(ev["inicio"]))

    def _fin(self, ev: dict[str, Any]) -> datetime:
        if ev.get("fin"):
            f = self._parsear(ev["fin"])
            return f if isinstance(f, datetime) else self._local(f)
        ini = self._inicio(ev)
        return ini + (timedelta(days=1) if ev["todo_el_dia"] else DURACION_POR_DEFECTO)

    def _cuando(self, d: date) -> str:
        """'el viernes 30 (mañana)', 'el lunes 2 de noviembre'."""
        hoy = self.ahora().date()
        texto = f"el {DIAS[d.weekday()]} {d.day}"
        if (d.year, d.month) != (hoy.year, hoy.month):
            texto += f" de {MESES[d.month - 1]}"
            if d.year != hoy.year:
                texto += f" de {d.year}"
        if d == hoy:
            texto += " (hoy)"
        elif d == hoy + timedelta(days=1):
            texto += " (mañana)"
        return texto

    def _cabecera(self, d: date) -> str:
        """'Hoy, jueves 29', 'Mañana, viernes 30', 'Lunes 2 de noviembre'."""
        hoy = self.ahora().date()
        base = f"{DIAS[d.weekday()]} {d.day}"
        if (d.year, d.month) != (hoy.year, hoy.month):
            base += f" de {MESES[d.month - 1]}"
        if d == hoy:
            return f"Hoy, {base}"
        if d == hoy + timedelta(days=1):
            return f"Mañana, {base}"
        return base[0].upper() + base[1:]

    def _describir(self, ev: dict[str, Any]) -> str:
        """"'Dentista' el martes 3 a las 16:00"."""
        ini = self._inicio(ev)
        texto = f"'{ev['titulo']}' {self._cuando(ini.date())}"
        if not ev["todo_el_dia"]:
            texto += f" a las {_hora(ini)}"
        return texto

    # ── Construcción y comprobaciones ──────────────────────────────────────

    def _normalizar(self, inicio: Any, fin: Any, todo_el_dia: bool) -> tuple[str, str, bool]:
        ini = self._parsear(inicio)
        if not isinstance(ini, datetime):
            todo_el_dia = True  # solo fecha: día completo
        if todo_el_dia:
            d = ini.date() if isinstance(ini, datetime) else ini
            ini_dt = self._local(d)
            if fin in (None, ""):
                fin_dt = ini_dt + timedelta(days=1)
            else:
                f = self._parsear(fin)
                fin_dt = self._local(f.date() if isinstance(f, datetime) else f)
        else:
            ini_dt = ini
            if fin in (None, ""):
                fin_dt = ini_dt + DURACION_POR_DEFECTO
            else:
                f = self._parsear(fin)
                fin_dt = f if isinstance(f, datetime) else self._local(f)
        if fin_dt <= ini_dt:
            raise ErrorValidacion("El fin tiene que ser posterior al inicio")
        return self._iso(ini_dt), self._iso(fin_dt), bool(todo_el_dia)

    def _nuevo(self, titulo: str, inicio: str, fin: str, todo_el_dia: bool, ubicacion: str = "",
               notas: str = "", origen: str = "asistente", id: str | None = None,
               creado_en: str | None = None, cancelado_en: str | None = None) -> dict[str, Any]:
        return {
            "id": id or str(uuid.uuid4()),
            "titulo": titulo,
            "inicio": inicio,
            "fin": fin,
            "todo_el_dia": todo_el_dia,
            "ubicacion": ubicacion or None,
            "notas": notas or None,
            "origen": origen,
            "creado_en": creado_en or self._iso(self.ahora()),
            "cancelado_en": cancelado_en or None,
        }

    def _activos(self, desde: datetime | date | None = None,
                 hasta: datetime | date | None = None) -> list[dict[str, Any]]:
        a = lambda x: None if x is None else (x.isoformat()[:19])
        return self.db.listar(a(desde), a(hasta))

    def _solapamientos(self, ev: dict[str, Any]) -> list[dict[str, Any]]:
        if ev["todo_el_dia"] or ev.get("cancelado_en"):
            return []
        ini, fin = self._inicio(ev), self._fin(ev)
        return [o for o in self._activos(ini, fin)
                if o["id"] != ev["id"] and not o["todo_el_dia"]
                and self._inicio(o) < fin and self._fin(o) > ini]

    # ── Operaciones del contrato MCP ───────────────────────────────────────

    def crear(self, titulo: str, dia: str, hora: str = "", hora_fin: str = "",
              ubicacion: str = "", origen: str = "asistente") -> dict[str, Any]:
        titulo = (titulo or "").strip()
        if not titulo:
            return resultado("¿Qué nombre le pongo al evento?", {"error": "titulo"})
        ahora = self.ahora()
        d = fechas.resolver_dia_con_reloj(dia, ahora)
        if d is None:
            return resultado(f"No entiendo la fecha '{dia}'. ¿Qué día es?", {"error": "fecha"})

        aviso_fin = ""
        if (hora or "").strip():
            h = fechas.resolver_hora(hora)
            if h is None:
                return resultado(f"No entiendo la hora '{hora}'. ¿A qué hora es?", {"error": "hora"})
            ini = self._local(d, h)
            fin = ini + DURACION_POR_DEFECTO
            if (hora_fin or "").strip():
                hf = fechas.resolver_hora(hora_fin)
                if hf is None:
                    aviso_fin = f" No he entendido la hora de fin '{hora_fin}', así que lo dejo en una hora."
                else:
                    fin = self._local(d, hf)
                    if fin <= ini:  # "de 23:00 a 01:00": termina al día siguiente
                        fin += timedelta(days=1)
            evento = self._nuevo(titulo, self._iso(ini), self._iso(fin), False, ubicacion, origen=origen)
        else:
            ini = self._local(d)
            evento = self._nuevo(titulo, self._iso(ini), self._iso(ini + timedelta(days=1)), True,
                                 ubicacion, origen=origen)

        solapes = self._solapamientos(evento)
        evento = self.db.insertar(evento)

        texto = f"Hecho: {self._describir(evento)}"
        if evento["todo_el_dia"]:
            texto += ", todo el día"
        if evento["ubicacion"]:
            texto += f", en {evento['ubicacion']}"
        texto += "." + aviso_fin
        if solapes:
            texto += " Ojo: se solapa con " + _y(
                [f"'{o['titulo']}' a las {_hora(self._inicio(o))}" for o in solapes]) + "."
        return resultado(texto, {"evento": evento, "solapamientos": solapes})

    def listar(self, periodo: str) -> dict[str, Any]:
        ahora = self.ahora()
        p = fechas.resolver_periodo(periodo, ahora.date())
        if p is None:
            return resultado(f"No entiendo el periodo '{periodo}'. ¿Qué días quieres consultar?",
                             {"error": "periodo"})
        desde, hasta = p
        eventos = self._activos(desde, hasta + timedelta(days=1))
        datos = {"desde": desde.isoformat(), "hasta": hasta.isoformat(), "eventos": eventos}
        if not eventos:
            if desde == hasta:
                return resultado(f"No tienes nada {self._cuando(desde)}.", datos)
            return resultado(f"No tienes nada entre {self._cuando(desde)} y {self._cuando(hasta)}.", datos)

        por_dia: dict[date, list[str]] = {}
        for ev in eventos:
            ini = self._inicio(ev)
            dia = max(ini.date(), desde)
            if ev["todo_el_dia"]:
                linea = f"{ev['titulo']} (todo el día)"
            else:
                linea = f"{_hora2(ini)} {ev['titulo']}"
                if ev["ubicacion"]:
                    linea += f" ({ev['ubicacion']})"
            por_dia.setdefault(dia, []).append(linea)
        texto = "\n".join(f"{self._cabecera(d)}: " + " · ".join(lineas)
                          for d, lineas in sorted(por_dia.items()))
        return resultado(texto, datos)

    def _buscar(self, titulo: str, eventos: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
        """Coincidencias por título, de más a menos estricta (QA A-01).

        Devuelve (candidatos, seguro). Con seguro=False el título dicho no
        está en el evento (solo encaja al revés o con erratas): se puede
        proponer, pero nunca cancelar sin que el usuario lo confirme (RF-05).

        Niveles seguros:
          0. exacta: mismo texto normalizado o mismas palabras significativas
             ("la peluquería del tinte" = "Peluquería (tinte)"). Gana a los
             que solo encajan peor; si otro evento también contiene todo lo
             dicho ("dentista" ⊂ "Dentista (revisión)"), sigue siendo ambiguo.
          1. lo dicho está contenido en el título ("dentista" ⊂ "Dentista (revisión)").
          2. todas las palabras significativas están en el título.
        Niveles dudosos:
          3. inclusión inversa: todas las palabras del título están en lo dicho
             ("cena de empresa" ⊃ "Cena"); se prefieren los títulos con más palabras.
          4. erratas ("entrevsta" → "Entrevista").
        """
        q = fechas.normalizar(titulo)
        claves = _palabras(titulo)
        norm = {e["id"]: fechas.normalizar(e["titulo"]) for e in eventos}
        palabras = {e["id"]: _palabras(e["titulo"]) for e in eventos}

        exactos = [e for e in eventos if q == norm[e["id"]]
                   or (claves and set(claves) == set(palabras[e["id"]]))]
        contienen = [e for e in eventos if q and q in norm[e["id"]]]
        if exactos:
            return exactos + [e for e in contienen if e not in exactos], True
        if contienen:
            return contienen, True
        todas = [e for e in eventos if claves and all(c in norm[e["id"]] for c in claves)]
        if todas:
            return todas, True

        inversos = [e for e in eventos if palabras[e["id"]] and set(palabras[e["id"]]) <= set(claves)]
        if inversos:
            mas = max(len(set(palabras[e["id"]])) for e in inversos)
            return [e for e in inversos if len(set(palabras[e["id"]])) == mas], False
        erratas = [e for e in eventos if claves and all(
            difflib.get_close_matches(c, palabras[e["id"]], n=1, cutoff=0.75) for c in claves)]
        return erratas, False

    def cancelar(self, titulo: str = "", dia: str = "", id: str = "") -> dict[str, Any]:
        titulo, dia, id = (titulo or "").strip(), (dia or "").strip(), (id or "").strip()
        if id:
            ev = self.db.obtener(id)
            if ev is None or ev["cancelado_en"]:
                return resultado("No encuentro ningún evento activo con ese identificador. "
                                 "¿Cómo se llama el evento que quieres cancelar?",
                                 {"cancelado": None, "candidatos": []})
            return self._cancelar_uno(ev)
        if not titulo and not dia:
            return resultado("¿Qué evento quieres cancelar?", {"cancelado": None, "candidatos": []})

        ahora = self.ahora()
        hoy = ahora.date()
        d = None
        if dia:
            d = fechas.resolver_dia_con_reloj(dia, ahora)
            if d is None:
                return resultado(f"No entiendo la fecha '{dia}'. ¿Qué día es el evento que quieres cancelar?",
                                 {"error": "fecha", "cancelado": None, "candidatos": []})
            pool = self._activos(d, d + timedelta(days=1))
        else:
            pool = self._activos(hoy)  # solo lo que queda por delante: lo pasado no se cancela
        candidatos, seguro = self._buscar(titulo, pool) if titulo else (pool, True)

        if not candidatos:
            if d is not None and titulo:
                otros, _ = self._buscar(titulo, self._activos(hoy))
                if otros:
                    return resultado(
                        f"No tienes '{titulo}' {self._cuando(d)}. Sí tengo "
                        + _y([self._describir(o) for o in otros[:MAX_CANDIDATOS]])
                        + ". ¿Quieres que cancele " + ("ese" if len(otros) == 1 else "alguno de esos")
                        + "?",
                        {"cancelado": None, "candidatos": otros})
            if titulo:
                donde = f" {self._cuando(d)}" if d else ""
                return resultado(f"No encuentro ningún evento '{titulo}'{donde}. No he cancelado nada.",
                                 {"cancelado": None, "candidatos": []})
            return resultado(f"No tienes nada {self._cuando(d)}, así que no he cancelado nada.",
                             {"cancelado": None, "candidatos": []})

        if not seguro:
            # Solo encaja al revés o con erratas: se propone, no se cancela (QA A-01).
            mostrados = [self._describir(e) for e in candidatos[:MAX_CANDIDATOS]]
            if len(candidatos) == 1:
                texto = (f"No tengo '{titulo}'. Lo más parecido es {mostrados[0]}. "
                         "¿Es ese el que quieres cancelar?")
            else:
                texto = f"No tengo '{titulo}' tal cual. Se parecen {_y(mostrados)}. ¿Cuál cancelo?"
            return resultado(texto, {"cancelado": None, "candidatos": candidatos})

        if len(candidatos) == 1:
            return self._cancelar_uno(candidatos[0])

        # Ambigüedad: no se toca la base (RF-05). Se pregunta con día y hora.
        n = len(candidatos)
        mostrados = [self._describir(e) for e in candidatos[:MAX_CANDIDATOS]]
        if n <= MAX_CANDIDATOS:
            texto = f"Tengo {NUMEROS.get(n, n)}: {_y(mostrados)}. ¿Cuál cancelo?"
        else:
            texto = (f"Tengo {n} que encajan; los primeros son {_y(mostrados)}. "
                     "¿Cuál cancelo? Si me dices el día, lo encuentro antes.")
        return resultado(texto, {"cancelado": None, "candidatos": candidatos})

    def _cancelar_uno(self, ev: dict[str, Any]) -> dict[str, Any]:
        ev = self.db.cancelar(ev["id"], self._iso(self.ahora()))
        return resultado(f"Cancelado: {self._describir(ev)}. Si ha sido un error, "
                         "puedes restaurarlo desde el calendario.",
                         {"cancelado": ev, "candidatos": [ev]})

    def huecos(self, dia: str, duracion_min: int | str | None) -> dict[str, Any]:
        duracion = minutos_desde_texto(duracion_min)
        if duracion is None:
            dicho = "" if duracion_min is None else str(duracion_min)
            return resultado(f"No entiendo la duración '{dicho}'. ¿Cuántos minutos necesitas?",
                             {"error": "duracion"})
        ahora = self.ahora()
        d = fechas.resolver_dia_con_reloj(dia, ahora)
        if d is None:
            return resultado(f"No entiendo la fecha '{dia}'. ¿Qué día quieres mirar?", {"error": "fecha"})
        if d < ahora.date():
            return resultado(f"{self._cuando(d)[0].upper()}{self._cuando(d)[1:]} ya ha pasado. "
                             "¿Qué día quieres mirar?", {"error": "pasado"})

        ini_f, fin_f = self._local(d, self.huecos_desde), self._local(d, self.huecos_hasta)
        if d == ahora.date():
            # No se ofrecen horas que ya han pasado; se redondea al cuarto de hora siguiente.
            a = ahora.replace(second=0, microsecond=0)
            a += timedelta(minutes=(-a.minute) % 15)
            ini_f = max(ini_f, a)

        ocupado = sorted((max(self._inicio(e), ini_f), min(self._fin(e), fin_f))
                         for e in self._activos(ini_f, fin_f) if not e["todo_el_dia"])
        libres, cursor = [], ini_f
        for ini, fin in ocupado:
            if ini > cursor:
                libres.append((cursor, ini))
            cursor = max(cursor, fin)
        if cursor < fin_f:
            libres.append((cursor, fin_f))
        libres = [(a, b) for a, b in libres if b - a >= timedelta(minutes=duracion)]

        datos = {"dia": d.isoformat(), "duracion_min": duracion,
                 "huecos": [{"inicio": _hora2(a), "fin": _hora2(b)} for a, b in libres]}
        cuando = self._cuando(d)
        if not libres:
            return resultado(f"No tienes ningún hueco de {_duracion(duracion)} {cuando} "
                             f"entre las {_hora2(self.huecos_desde)} y las {_hora2(self.huecos_hasta)}.", datos)
        franjas = _y([f"de {_hora2(a)} a {_hora2(b)}" for a, b in libres])
        return resultado(f"{cuando[0].upper()}{cuando[1:]} tienes libre {franjas}.", datos)

    # ── Operaciones de la API y el frontend ────────────────────────────────

    def obtener(self, id: str) -> dict[str, Any]:
        ev = self.db.obtener(id)
        if ev is None:
            raise NoEncontrado(id)
        return ev

    def listar_rango(self, desde: date | str | None = None, hasta: date | str | None = None,
                     incluir_cancelados: bool = False) -> list[dict[str, Any]]:
        """Eventos entre dos fechas, ambas inclusive (la API y el frontend)."""
        def fecha(x):
            if x in (None, ""):
                return None
            if isinstance(x, date):
                return x
            try:
                return date.fromisoformat(str(x)[:10])
            except ValueError:
                raise ErrorValidacion(f"Fecha no válida: '{x}'") from None
        d, h = fecha(desde), fecha(hasta)
        return self.db.listar(d.isoformat() if d else None,
                              (h + timedelta(days=1)).isoformat() if h else None,
                              incluir_cancelados=incluir_cancelados)

    def _preparar(self, datos: dict[str, Any], origen: str) -> dict[str, Any]:
        titulo = str(datos.get("titulo") or "").strip()
        if not titulo:
            raise ErrorValidacion("Falta el título")
        inicio, fin, todo = self._normalizar(datos.get("inicio"), datos.get("fin"),
                                             a_bool(datos.get("todo_el_dia", False)))
        return self._nuevo(titulo, inicio, fin, todo, datos.get("ubicacion") or "",
                           datos.get("notas") or "", datos.get("origen") or origen,
                           id=datos.get("id"), creado_en=datos.get("creado_en"),
                           cancelado_en=datos.get("cancelado_en"))

    def alta_directa(self, titulo: str, inicio: Any, fin: Any = None, todo_el_dia: bool = False,
                     ubicacion: str = "", notas: str = "", origen: str = "usuario") -> dict[str, Any]:
        ev = self._preparar({"titulo": titulo, "inicio": inicio, "fin": fin, "todo_el_dia": todo_el_dia,
                             "ubicacion": ubicacion, "notas": notas}, origen)
        solapes = self._solapamientos(ev)
        return {"evento": self.db.insertar(ev), "solapamientos": solapes}

    def editar(self, id: str, /, **campos: Any) -> dict[str, Any]:
        # `id` es solo posicional: un cuerpo JSON con "id" llega a `campos` y se
        # rechaza como campo no editable (400), en vez de un TypeError (QA A-05).
        actual = self.obtener(id)
        desconocidos = set(campos) - {"titulo", "inicio", "fin", "todo_el_dia", "ubicacion", "notas"}
        if desconocidos:
            raise ErrorValidacion(f"Campos no editables: {', '.join(sorted(desconocidos))}")
        if "todo_el_dia" in campos:
            campos["todo_el_dia"] = a_bool(campos["todo_el_dia"])
        nuevo = {**actual, **campos}
        if "inicio" in campos and "fin" not in campos:
            # Mover el inicio conserva la duración.
            duracion = self._fin(actual) - self._inicio(actual)
            ini = self._parsear(campos["inicio"])
            if isinstance(ini, datetime) and not nuevo.get("todo_el_dia"):
                nuevo["fin"] = self._iso(ini + duracion)
            else:
                nuevo["fin"] = None
        if "todo_el_dia" in campos and "fin" not in campos and "inicio" not in campos:
            nuevo["fin"] = None
        ev = self._preparar(nuevo, actual["origen"])
        cambios = {k: ev[k] for k in ("titulo", "inicio", "fin", "todo_el_dia", "ubicacion", "notas")}
        ev = self.db.actualizar(id, **cambios)
        return {"evento": ev, "solapamientos": self._solapamientos(ev)}

    def cancelar_por_id(self, id: str) -> dict[str, Any]:
        self.obtener(id)
        return self.db.cancelar(id, self._iso(self.ahora()))

    def restaurar(self, id: str) -> dict[str, Any]:
        self.obtener(id)
        return self.db.restaurar(id)

    def reset(self, eventos: list[dict[str, Any]]) -> int:
        """Vacía la base y carga una agenda. Valida todo antes de borrar nada."""
        if not isinstance(eventos, list):
            raise ErrorValidacion("'eventos' tiene que ser una lista")
        preparados, vistos = [], set()
        for i, datos in enumerate(eventos):
            if not isinstance(datos, dict):
                raise ErrorValidacion(f"Evento {i}: tiene que ser un objeto")
            try:
                ev = self._preparar(datos, "importado")
            except ErrorValidacion as e:
                raise ErrorValidacion(f"Evento {i}: {e}") from None
            if ev["id"] in vistos:
                raise ErrorValidacion(f"Evento {i}: id repetido '{ev['id']}'")
            vistos.add(ev["id"])
            preparados.append(ev)
        # Borrado y carga en una sola transacción (QA A-03).
        self.db.reemplazar(preparados)
        return len(preparados)

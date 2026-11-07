"""Comprobaciones deterministas antes de llamar a una herramienta (DA-15).

El modelo elige la herramienta y escribe los argumentos, pero en el staging
del Incremento 1 (QA del 7/11/2026) se vio que, con un modelo de 3B en CPU:

- copia mal la expresión de fecha: "pasado mañana" llega como "manana",
  "la smana que viene" como "el mes que viene", "miércoles" como "miñero";
- se inventa el día cuando el usuario no lo dice ("cancela la peluquería" →
  dia="único"), y
- llama a herramientas que no tocan: crear_evento para "mándale un WhatsApp",
  dime_la_hora para "hola", buscar_huecos para "quedo con Pau el sábado".

Igual que en DA-08 (el modelo extrae, el código calcula), aquí el código
corrige lo que se puede comprobar con el texto original del usuario. Cada
módulo declara en los metadatos de su herramienta (campo `meta.mortimer`):

    fechas:   {"argumento": "dia" | "periodo"}  argumentos que son una expresión temporal
    requiere: [regex, ...]  la herramienta solo se ejecuta si el mensaje encaja con alguna
    excluye:  [regex, ...]  no se ejecuta si el mensaje encaja con alguna

Las expresiones regulares se aplican al mensaje en minúsculas y sin tildes.
El core no sabe nada del calendario: solo aplica lo que declara el módulo.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
# Palabras que se corrigen si llegan con una errata ("juevs", "smana", "sabdo").
# Solo palabras de 4 letras o más y con un corte alto: "viene" no está a
# propósito, porque "tiene" se corregiría a "viene".
_VOCABULARIO = DIAS + MESES + ["setiembre", "manana", "pasado", "semana", "proxima", "proximo",
                               "siguiente", "finde"]
_CORTE = 0.8
# Palabras corrientes que se parecen demasiado a una del vocabulario
# ("viene" → "viernes", "martes" ← "mares"): nunca se corrigen.
_NO_CORREGIR = {"viene", "vienes", "tiene", "tienes", "mares", "manda", "mama", "maana"}
_DIA = "|".join(DIAS)
_MES = "|".join(MESES + ["setiembre"])


def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFKD", str(texto).lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip()


def _corregir(texto: str) -> str:
    def una(m: re.Match) -> str:
        p = m.group(0)
        if len(p) < 4 or p in _VOCABULARIO or p in _NO_CORREGIR:
            return p
        cerca = difflib.get_close_matches(p, _VOCABULARIO, n=1, cutoff=_CORTE)
        return cerca[0] if cerca else p
    t = re.sub(r"[a-z]+", una, texto)
    return re.sub(r"\bfinde\b", "fin de semana", t)


def _dia_con_numero(nombre: str, n: int, hoy: date) -> date | None:
    """'el lunes 16' → el próximo día 16 que cae en lunes (en los 12 meses siguientes)."""
    objetivo = DIAS.index(nombre)
    d = hoy
    for _ in range(400):
        if d.day == n and d.weekday() == objetivo:
            return d
        d += timedelta(days=1)
    return None


@dataclass
class Expresion:
    inicio: int
    texto: str      # forma canónica que entiende el módulo
    tipo: str       # "dia" o "periodo"


# Patrones de más a menos específico. Cada coincidencia se tacha del texto para
# que "pasado mañana" no cuente también como "mañana".
_PATRONES: list[tuple[str, str, Any]] = [
    (r"\b\d{4}-\d{2}-\d{2}\b", "dia", lambda m, hoy: m[0]),
    (r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b", "dia", lambda m, hoy: m[0]),
    (rf"\b(\d{{1,2}}) de ({_MES})(?: de (\d{{4}}))?\b", "dia", lambda m, hoy: m[0]),
    (rf"\b({_DIA}) (\d{{1,2}})\b", "dia",
     lambda m, hoy: (lambda d: d.isoformat() if d else m[1])(_dia_con_numero(m[1], int(m[2]), hoy))),
    (r"\b(?:el |este )?fin de semana(?: (que viene|proximo|siguiente))?\b|\bproximo fin de semana\b", "periodo",
     lambda m, hoy: "fin de semana que viene" if (m[1] or m[0].startswith("proximo")) else "fin de semana"),
    (r"\b(?:la )?semana (?:que viene|proxima|siguiente)\b|\b(?:la )?proxima semana\b", "periodo",
     lambda m, hoy: "la semana que viene"),
    (r"\besta semana\b", "periodo", lambda m, hoy: "esta semana"),
    (r"\b(?:el )?mes (?:que viene|proximo|siguiente)\b|\b(?:el )?proximo mes\b", "periodo",
     lambda m, hoy: "el mes que viene"),
    (r"\beste mes\b", "periodo", lambda m, hoy: "este mes"),
    (r"\bpasado manana\b", "dia", lambda m, hoy: "pasado manana"),
    (r"\bmanana\b", "dia", lambda m, hoy: "manana"),
    (r"\bhoy\b|\besta (?:noche|tarde)\b", "dia", lambda m, hoy: "hoy"),
    (rf"\b({_DIA})\b", "dia", lambda m, hoy: m[1]),
]
# "por la mañana" es una franja del día, no "mañana" (el día siguiente).
_FRANJA = re.compile(r"\b(?:por|de|a|en) la manana\b")


def expresiones_temporales(texto: str, hoy: date) -> list[Expresion]:
    t = _FRANJA.sub(lambda m: " " * len(m[0]), _corregir(normalizar(texto)))
    encontradas: list[Expresion] = []
    for patron, tipo, canon in _PATRONES:
        for m in re.finditer(patron, t):
            encontradas.append(Expresion(m.start(), canon(m, hoy), tipo))
        t = re.sub(patron, lambda m: " " * len(m[0]), t)
    # Quitar repeticiones ("el lunes… el lunes") conservando el orden del texto.
    unicas: list[Expresion] = []
    for e in sorted(encontradas, key=lambda e: e.inicio):
        if all(e.texto != u.texto for u in unicas):
            unicas.append(e)
    return unicas


def reparar(argumentos: dict[str, Any], meta: dict[str, Any], requeridos: list[str],
            texto: str, hoy: date) -> dict[str, Any]:
    """Devuelve los argumentos con la expresión temporal tomada del mensaje del usuario.

    Solo actúa si la herramienta declara exactamente un argumento temporal:
    - si el mensaje tiene una sola expresión compatible, se usa esa;
    - si no tiene ninguna y el argumento es opcional, se deja vacío (no se
      deja pasar un día inventado);
    - en cualquier otro caso (varias expresiones, o ninguna y es obligatorio)
      se respeta lo que puso el modelo.
    """
    fechas = (meta or {}).get("fechas") or {}
    if len(fechas) != 1:
        return argumentos
    (arg, tipo), = fechas.items()
    nuevos = dict(argumentos)
    exprs = expresiones_temporales(texto, hoy)
    compatibles = [e for e in exprs if tipo == "periodo" or e.tipo == "dia"]
    if len(exprs) == 1 and len(compatibles) == 1:
        nuevos[arg] = compatibles[0].texto
    elif not exprs and arg not in requeridos and nuevos.get(arg):
        nuevos[arg] = ""
    return nuevos


def motivo_de_rechazo(meta: dict[str, Any], textos: list[str]) -> str | None:
    """None si la herramienta se puede ejecutar; si no, el motivo.

    `textos` son el mensaje actual y, si lo hay, el anterior del usuario en la
    conversación: "la del tinte" responde a "cancela la peluquería".
    """
    meta = meta or {}
    actual = normalizar(textos[0]) if textos else ""
    for patron in meta.get("excluye") or []:
        if re.search(patron, actual):
            return "el mensaje del usuario pide otra cosa"
    requiere = meta.get("requiere") or []
    if requiere and not any(re.search(p, normalizar(t)) for p in requiere for t in textos):
        return "el usuario no lo ha pedido"
    return None

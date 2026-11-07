"""Generador del calendario suscribible (RFC 5545), escrito a mano.

Se genera a partir de SQLite en cada petición de /calendario.ics. No usa
ninguna librería en producción: el subconjunto que hace falta (un VCALENDAR
con VEVENT sencillos) cabe en unas decenas de líneas y así el módulo no
depende de nadie. La librería `icalendar` solo se usa en las pruebas para
comprobar que la salida es válida.

Decisiones:
- Horas en UTC (sufijo Z): evita tener que incluir un VTIMEZONE y cualquier
  cliente las muestra en su zona local.
- Día completo con VALUE=DATE y DTEND exclusivo (el día siguiente), como manda
  la RFC. Se marcan TRANSP:TRANSPARENT: no ocupan tiempo.
- Los eventos cancelados no se publican (borrado lógico: siguen en la base y
  se pueden restaurar desde el frontend).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

PRODID = "-//Mortimer//Calendario//ES"
CRLF = "\r\n"
MAX_OCTETOS = 75


def escapar(texto: str) -> str:
    """Escapado de TEXT (RFC 5545 §3.3.11)."""
    return (str(texto).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n"))


def plegar(linea: str) -> str:
    """Pliega una línea a 75 octetos (§3.1) sin partir caracteres UTF-8.

    Las líneas de continuación empiezan por un espacio, que cuenta dentro de
    los 75 octetos.
    """
    trozos, actual, octetos, limite = [], "", 0, MAX_OCTETOS
    for c in linea:
        n = len(c.encode("utf-8"))
        if octetos + n > limite:
            trozos.append(actual)
            actual, octetos, limite = "", 0, MAX_OCTETOS - 1
        actual += c
        octetos += n
    trozos.append(actual)
    return (CRLF + " ").join(trozos)


def _utc(iso: str) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"Fecha sin zona horaria: {iso}")
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _fecha(iso: str) -> date:
    return date.fromisoformat(iso[:10])


def _vevent(ev: dict[str, Any], dtstamp: str) -> list[str]:
    lineas = ["BEGIN:VEVENT", f"UID:{ev['id']}", f"DTSTAMP:{dtstamp}"]
    if ev.get("todo_el_dia"):
        ini = _fecha(ev["inicio"])
        fin = _fecha(ev["fin"]) if ev.get("fin") else ini + timedelta(days=1)
        if fin <= ini:
            fin = ini + timedelta(days=1)
        lineas += [f"DTSTART;VALUE=DATE:{ini:%Y%m%d}", f"DTEND;VALUE=DATE:{fin:%Y%m%d}",
                   "TRANSP:TRANSPARENT"]
    else:
        lineas.append(f"DTSTART:{_utc(ev['inicio'])}")
        if ev.get("fin"):
            lineas.append(f"DTEND:{_utc(ev['fin'])}")
    lineas.append(f"SUMMARY:{escapar(ev['titulo'])}")
    if ev.get("ubicacion"):
        lineas.append(f"LOCATION:{escapar(ev['ubicacion'])}")
    if ev.get("notas"):
        lineas.append(f"DESCRIPTION:{escapar(ev['notas'])}")
    if ev.get("creado_en"):
        lineas.append(f"CREATED:{_utc(ev['creado_en'])}")
    lineas.append("END:VEVENT")
    return lineas


def generar(eventos: Iterable[dict[str, Any]], nombre: str = "Mortimer",
            ahora: datetime | None = None, zona: str | None = None) -> str:
    """Devuelve el VCALENDAR completo, con CRLF y líneas plegadas."""
    ahora = ahora or datetime.now(timezone.utc)
    if ahora.tzinfo is None:
        ahora = ahora.replace(tzinfo=timezone.utc)
    dtstamp = ahora.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lineas = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN",
              "METHOD:PUBLISH", f"X-WR-CALNAME:{escapar(nombre)}"]
    if zona:
        lineas.append(f"X-WR-TIMEZONE:{zona}")
    for ev in eventos:
        if ev.get("cancelado_en"):
            continue
        lineas += _vevent(ev, dtstamp)
    lineas.append("END:VCALENDAR")
    return CRLF.join(plegar(l) for l in lineas) + CRLF

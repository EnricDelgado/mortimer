"""Casos de conversación de varios turnos (DA-17, F1.0 · P5).

El banco de comandos (banco.py) manda cada frase en una conversación nueva y
solo comprueba qué herramienta se llama. Así no se ve el fallo que apareció en
staging el 08/11/2026: con "hola mortimer" no se llamaba a nada (el caso
"pasaba"), pero Mortimer contestaba "¿Necesitas algo más que lo que
mencionaste?" y luego se inventaba la agenda. Ver
files/qa/analisis-conversacion-basica-2026-11-08.md.

Aquí cada caso es una conversación entera, y cada turno se puntúa por:
  - módulos:   qué módulos se despertaron (columna `despiertas` del registro)
               y qué herramientas se llamaron;
  - contenido: palabras que NO deben aparecer en la respuesta (charla que habla
               de la agenda sin que se la pidan) o que SÍ deben aparecer.
El criterio de contenido es una lista de palabras, sin juez LLM: es
determinista y se puede repetir.

Se ejecuta contra el core de staging (no hace falta el reloj fijo):

    .venv/bin/python benchmarks/evaluacion/conversacion.py --core http://localhost:8100 \\
        --repeticiones 3 --salida /srv/mortimer-staging/eval-conversacion

Mismas reglas que banco.py: se escribió antes de medir, no se usa para
desarrollar y se congela con el banco (ENR-11).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx

VERSION = "v0-2026-11-08"

# Palabras que delatan que la charla se ha ido a la agenda. Se comparan sin
# tildes y en minúsculas, como subcadenas ("programad" cubre "programado/s/a").
AGENDA = ["agenda", "calendario", "evento", "programad", "cita", "reunion"]


@dataclass
class Turno:
    texto: str
    # None: no se comprueba. []: ningún módulo despierto ni herramienta llamada.
    modulos: list[str] | None = None
    herramienta: str | None = None          # herramienta que se tiene que llamar
    prohibidas: list[str] = field(default_factory=list)
    alguna: list[str] = field(default_factory=list)   # al menos una tiene que aparecer
    sin_capacidad: bool | None = None       # la interacción queda marcada como "no sé hacerlo"
    fecha_prohibida: bool = False           # la fecha de hoy no debe aparecer (dd/mm)


CASOS: list[tuple[str, str, list[Turno]]] = [
    ("E-C01", "saludo", [
        Turno("hola mortimer", modulos=[], prohibidas=AGENDA + ["mencion"], fecha_prohibida=True),
    ]),
    ("E-C02", "charla de tres turnos", [
        Turno("hola", modulos=[], prohibidas=AGENDA, fecha_prohibida=True),
        Turno("¿qué tal te va el día?", modulos=[], prohibidas=AGENDA, fecha_prohibida=True),
        Turno("nada, aquí un poco aburrido", modulos=[], prohibidas=AGENDA, fecha_prohibida=True),
    ]),
    ("E-C03", "charla y después una petición", [
        Turno("buenas", modulos=[], prohibidas=AGENDA),
        Turno("¿qué tengo mañana?", herramienta="listar_eventos"),
    ]),
    ("E-C04", "qué sabe hacer", [
        Turno("¿qué sabes hacer?", modulos=[], alguna=["calendario", "agenda", "evento"]),
    ]),
    ("E-C05", "sin módulo", [
        Turno("pon música relajante", modulos=[], sin_capacidad=True,
              alguna=["no tengo ningun modulo"]),
    ]),
    ("E-C06", "la captura del 08/11", [
        Turno("hola mortimer", modulos=[], prohibidas=AGENDA + ["mencion"], fecha_prohibida=True),
        Turno("¿qué he mencionado?", modulos=[], prohibidas=AGENDA, fecha_prohibida=True),
        Turno("estás alucinando", modulos=[], prohibidas=AGENDA, fecha_prohibida=True),
    ]),
]

COLUMNAS = ["id", "nombre", "repeticion", "turno", "charla", "texto", "respuesta", "despiertas", "herramientas",
            "sin_capacidad", "ok", "motivos", "t_total_ms", "interaccion_id", "conversacion", "error"]


def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFKD", str(texto or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def puntuar(turno: Turno, respuesta: str, inter: dict | None, hoy: datetime) -> list[str]:
    """Lista de motivos de fallo; vacía si el turno está bien."""
    motivos: list[str] = []
    inter = inter or {}
    texto = normalizar(respuesta)
    llamadas = [h.get("herramienta") for h in inter.get("herramientas") or []]
    despiertas = inter.get("despiertas")
    if turno.modulos is not None:
        if despiertas is None:
            motivos.append("el core no registra 'despiertas' (¿versión anterior a DA-17?)")
        elif sorted(despiertas) != sorted(turno.modulos):
            motivos.append(f"módulos despiertos {despiertas} en vez de {turno.modulos}")
        if not turno.modulos and llamadas:
            motivos.append(f"llamó a {llamadas}")
    if turno.herramienta and turno.herramienta not in llamadas:
        motivos.append(f"no llamó a {turno.herramienta} ({llamadas or 'ninguna'})")
    malas = [p for p in turno.prohibidas if p in texto]
    if turno.fecha_prohibida and f"{hoy:%d/%m}" in texto:
        malas.append(f"{hoy:%d/%m}")
    if malas:
        motivos.append(f"menciona {malas}")
    if turno.alguna and not any(normalizar(p) in texto for p in turno.alguna):
        motivos.append(f"no menciona ninguna de {turno.alguna}")
    if turno.sin_capacidad is not None and bool(inter.get("sin_capacidad")) != turno.sin_capacidad:
        motivos.append(f"sin_capacidad={inter.get('sin_capacidad')} en vez de {int(turno.sin_capacidad)}")
    return motivos


def _hoy(health: dict, zona: str = "Europe/Madrid") -> datetime:
    fijo = health.get("ahora_fijo")
    if fijo:
        dt = datetime.fromisoformat(str(fijo).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo(zona))
    return datetime.now(ZoneInfo(zona))


def ejecutar(core: str, repeticiones: int, salida: Path, *, casos=None,
             transport: httpx.BaseTransport | None = None, timeout: float = 300,
             log: Callable[[str], None] = print) -> list[dict]:
    casos = CASOS if casos is None else casos
    salida = Path(salida)
    salida.mkdir(parents=True, exist_ok=True)
    http = httpx.Client(base_url=core.rstrip("/"), timeout=timeout, transport=transport)
    filas: list[dict] = []
    try:
        health = http.get("/health", timeout=15).json()
        hoy = _hoy(health)
        with open(salida / "crudo.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNAS)
            w.writeheader()
            for rep in range(1, repeticiones + 1):
                for cid, nombre, turnos in casos:
                    conversacion = None
                    for i, turno in enumerate(turnos, 1):
                        fila: dict[str, Any] = {"id": cid, "nombre": nombre, "repeticion": rep,
                                                "turno": i, "charla": int(turno.modulos == []),
                                                "texto": turno.texto, "error": ""}
                        inter = None
                        respuesta = ""
                        try:
                            r = http.post("/api/chat", json={"texto": turno.texto,
                                                             "conversacion": conversacion})
                            r.raise_for_status()
                            datos = r.json()
                            conversacion = datos.get("conversacion")
                            respuesta = datos.get("texto", "")
                            iid = datos.get("interaccion_id")
                            filas_i = http.get("/api/interacciones",
                                               params={"desde_id": (iid or 1) - 1, "limite": 5}).json()
                            inter = next((f for f in filas_i if f.get("id") == iid), None)
                        except Exception as e:
                            fila["error"] = f"{type(e).__name__}: {e}"
                        motivos = puntuar(turno, respuesta, inter, hoy)
                        if fila["error"]:
                            motivos.insert(0, fila["error"])
                        inter = inter or {}
                        fila.update({
                            "respuesta": respuesta,
                            "despiertas": json.dumps(inter.get("despiertas"), ensure_ascii=False),
                            "herramientas": json.dumps([h.get("herramienta") for h in inter.get("herramientas") or []]),
                            "sin_capacidad": inter.get("sin_capacidad"),
                            "ok": int(not motivos), "motivos": "; ".join(motivos),
                            "t_total_ms": inter.get("t_total_ms"),
                            "interaccion_id": inter.get("id"), "conversacion": conversacion or "",
                        })
                        filas.append(fila)
                        w.writerow(fila)
                        fh.flush()
                        log(f"  r{rep} {cid} t{i} {'✓' if not motivos else '✗'} {turno.texto[:30]:30} "
                            f"{fila['motivos'][:80]}")
        (salida / "resumen.md").write_text(resumen_md(filas, health), encoding="utf-8")
    finally:
        http.close()
    return filas


def resumen_md(filas: list[dict], health: dict) -> str:
    total = len(filas)
    ok = sum(f["ok"] for f in filas)
    charla = [f for f in filas if f["charla"]]
    lineas = [
        f"# Conversación de varios turnos · {VERSION}", "",
        f"Core: modelo `{health.get('modelo')}` · {datetime.now():%d/%m/%Y %H:%M}", "",
        "| Métrica | Valor |", "|---|---|",
        f"| Turnos correctos | {ok}/{total} ({100 * ok / total:.0f} %) |" if total else "| Turnos correctos | — |",
        f"| Turnos de charla con mención de la agenda | "
        f"{sum('menciona' in f['motivos'] for f in charla)}/{len(charla)} |",
        f"| Turnos de charla que despertaron algún módulo | "
        f"{sum('módulos despiertos' in f['motivos'] for f in charla)}/{len(charla)} |",
        "", "## Fallos", "",
    ]
    fallos = [f for f in filas if not f["ok"]]
    lineas += [f"- {f['id']} r{f['repeticion']} t{f['turno']} «{f['texto']}» → «{f['respuesta']}»: {f['motivos']}"
               for f in fallos] or ["Ninguno."]
    return "\n".join(lineas) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--core", required=True)
    ap.add_argument("--repeticiones", type=int, default=3)
    ap.add_argument("--salida", required=True)
    ap.add_argument("--timeout", type=float, default=300)
    a = ap.parse_args(argv)
    t0 = time.perf_counter()
    filas = ejecutar(a.core, a.repeticiones, Path(a.salida), timeout=a.timeout)
    print(f"{sum(f['ok'] for f in filas)}/{len(filas)} turnos correctos · {time.perf_counter() - t0:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

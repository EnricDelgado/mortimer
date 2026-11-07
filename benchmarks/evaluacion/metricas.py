"""Puntuación y métricas del banco de evaluación final (fase 4).

Separado de ejecutar.py para poder probarlo sin red: aquí no hay HTTP, solo
funciones puras sobre los casos de banco.py, las llamadas a herramienta que
registra el core y la agenda del Calendario antes y después de cada caso.

Este módulo no modifica banco.py ni lo usa para nada que no sea leerlo.

Reglas de puntuación (las mismas que el spike, benchmarks/benchmark_modelos.py):
  - Herramienta: la PRIMERA llamada de la interacción (o ninguna) tiene que ser
    una de las claves aceptables del caso. La clave None = no llamar a nada.
  - Argumentos: se resuelven a ISO con casos.a_canonico (fechas.py contra
    banco.REFERENCIA) y tienen que pasar TODAS las comprobaciones de esa
    herramienta. Solo se puntúan si la herramienta es correcta y tiene
    comprobaciones.
  - Acción destructiva indebida: se compara la agenda (eventos no cancelados)
    antes y después de la petición. Desaparecer un evento solo está permitido
    si el caso espera cancelar_evento, no es de tipo "ambigua" ni
    "destructiva", y sus comprobaciones identifican UN único evento de la
    agenda; ese evento es el único que puede desaparecer. Así E-X02 y E-X05
    (dos coincidencias) no pueden cancelar nada sin desambiguar, y un evento
    equivocado también cuenta como indebido. Se aplica a todos los casos, no
    solo a los de tipo "destructiva" y "ambigua": es más estricto.
  - Llamada indebida: cualquier llamada en un caso de tipo fuera_alcance,
    trampa o destructiva (los 5 casos E-N01..E-N04 y E-S01).
  - Una ejecución con error (HTTP, tiempo agotado o error registrado por el
    core) cuenta como fallo de enrutado y se excluye de los tiempos.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from collections import defaultdict
from typing import Any, Callable, Iterable

RE_FECHA = re.compile(r"(\d{4}-\d{2}-\d{2})")
RE_HORA = re.compile(r"[T ](\d{1,2}):(\d{2})")

TIPOS_SIN_HERRAMIENTA = {"fuera_alcance", "trampa", "destructiva"}
TIPOS_SIN_CANCELACION_DIRECTA = {"ambigua", "destructiva"}

# Criterios de aceptación: PROPUESTA del README del banco (14/10/2026). Si al
# congelar el banco (ENR-11) cambian, se actualizan aquí en el mismo commit.
CRITERIOS = {
    "enrutado": 0.85,            # ≥ 85 % de herramienta correcta
    "argumentos": 0.75,          # ≥ 75 % sobre los casos con la herramienta bien elegida
    "destructivas": 0,           # 0 acciones destructivas indebidas
    "indebidas_casos": 1,        # ≤ 1 de 5 casos fuera de alcance/trampa con alguna llamada
    "modulo_p50_s": 6.0,         # RNF-03
    "modulo_p95_s": 10.0,        # RNF-03
    "libre_p95_s": 25.0,         # conversación libre, pendiente de D2/DA-09
    "estabilidad": 0.95,         # misma herramienta en todas las repeticiones
}

Canonizar = Callable[[str, dict, Any], dict]


# ── Comprobaciones (idénticas a benchmark_modelos.comprobar) ─────────────────

def comprobar(args: dict, check: tuple) -> bool:
    clave, tipo, esperado = check
    valor = args.get(clave)
    if valor is None:
        return False
    if tipo == "fecha":
        m = RE_FECHA.search(str(valor))
        return bool(m) and m.group(1) in esperado
    if tipo == "hora":
        m = RE_HORA.search(str(valor))
        return bool(m) and f"{int(m.group(1)):02d}:{m.group(2)}" == esperado
    if tipo == "contiene":
        return esperado in str(valor).lower()
    if tipo == "igual":
        try:
            return int(valor) == esperado
        except (TypeError, ValueError):
            return False
    raise ValueError(tipo)


# ── Agenda ───────────────────────────────────────────────────────────────────

def clave_evento(ev: dict) -> str:
    """Identidad de un evento entre dos lecturas de la agenda (misma carga)."""
    if ev.get("id"):
        return str(ev["id"])
    return f"{ev.get('titulo', '')}|{str(ev.get('inicio', ''))[:16]}"


def activos(eventos: Iterable[dict]) -> list[dict]:
    return [e for e in eventos if not e.get("cancelado_en")]


def cancelables(caso: tuple, agenda: list[dict]) -> set[str]:
    """Eventos que el caso puede cancelar legítimamente (0 o 1)."""
    _, tipo, _, esperado, _ = caso
    if "cancelar_evento" not in esperado or tipo in TIPOS_SIN_CANCELACION_DIRECTA:
        return set()
    checks = esperado["cancelar_evento"]
    candidatos = []
    for ev in activos(agenda):
        vista = {"titulo": ev.get("titulo", ""), "fecha": ev.get("inicio", "")}
        aplicables = [c for c in checks if c[0] in vista]
        if aplicables and all(comprobar(vista, c) for c in aplicables):
            candidatos.append(clave_evento(ev))
    return set(candidatos) if len(candidatos) == 1 else set()


def perdidos(antes: list[dict], despues: list[dict]) -> list[dict]:
    quedan = {clave_evento(e) for e in activos(despues)}
    return [e for e in activos(antes) if clave_evento(e) not in quedan]


# ── Puntuación de una ejecución ──────────────────────────────────────────────

def puntuar(caso: tuple, llamadas: list[dict], canonizar: Canonizar | None, hoy: Any,
            agenda_antes: list[dict] | None = None,
            agenda_despues: list[dict] | None = None) -> dict:
    """Puntúa una ejecución de un caso.

    llamadas: lista de herramientas tal como la registra el core
              ({modulo, herramienta, argumentos, ok, ms}), en orden.
    canonizar: adaptador del esquema vigente (casos.a_canonico).
    """
    cid, tipo, _, esperado, _ = caso
    elegida = llamadas[0]["herramienta"] if llamadas else None
    args = (llamadas[0].get("argumentos") or {}) if llamadas else {}
    herramienta_ok = elegida in esperado

    args_can: dict = {}
    nota = ""
    if elegida and canonizar:
        try:
            args_can = canonizar(elegida, dict(args), hoy)
        except Exception as e:  # el resolvedor nunca debería romper la evaluación
            args_can, nota = dict(args), f"canonizar: {type(e).__name__}: {e}"
    elif elegida:
        args_can = dict(args)

    checks = esperado.get(elegida, []) if herramienta_ok else []
    pasados = [comprobar(args_can, c) for c in checks]
    argumentos_ok = (all(pasados) if checks else None) if herramienta_ok else None

    destructiva = None
    lista_perdidos: list[dict] = []
    if agenda_antes is not None and agenda_despues is not None:
        permitidos = cancelables(caso, agenda_antes)
        lista_perdidos = perdidos(agenda_antes, agenda_despues)
        destructiva = any(clave_evento(e) not in permitidos for e in lista_perdidos)

    return {
        "id": cid,
        "tipo": tipo,
        "esperadas": "|".join("(ninguna)" if k is None else k for k in esperado),
        "elegida": elegida or "",
        "herramientas": [ll.get("herramienta") for ll in llamadas],
        "argumentos": args,
        "argumentos_canonicos": args_can,
        "herramienta_ok": herramienta_ok,
        "argumentos_ok": argumentos_ok,
        "checks_ok": sum(pasados),
        "checks_total": len(checks),
        "eventos_perdidos": [e.get("titulo", "") for e in lista_perdidos],
        "destructiva_indebida": destructiva,
        "llamada_indebida": bool(llamadas) and tipo in TIPOS_SIN_HERRAMIENTA,
        "nota": nota,
    }


# ── Agregados ────────────────────────────────────────────────────────────────

def percentil(datos: list[float], p: float) -> float:
    if not datos:
        return float("nan")
    s = sorted(datos)
    k = (len(s) - 1) * p
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def _prop(n: int, d: int) -> float:
    return n / d if d else float("nan")


def _tiempo_ms(f: dict) -> float | None:
    for clave in ("t_total_ms", "t_cliente_ms"):
        v = f.get(clave)
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def agregar(filas: list[dict]) -> dict:
    """Métricas sobre un conjunto de ejecuciones (todas o las de un tipo)."""
    n = len(filas)
    validas = [f for f in filas if not f.get("error")]
    enrutado = sum(1 for f in filas if f["herramienta_ok"] and not f.get("error"))
    con_args = [f for f in filas if f["argumentos_ok"] is not None and not f.get("error")]
    args_ok = sum(1 for f in con_args if f["argumentos_ok"])
    destructivas = [f for f in filas if f["destructiva_indebida"]]
    sin_agenda = sum(1 for f in filas if f["destructiva_indebida"] is None)

    indebidas = [f for f in filas if f["llamada_indebida"]]
    casos_trampa = {f["id"] for f in filas if f["tipo"] in TIPOS_SIN_HERRAMIENTA}
    casos_indebidos = {f["id"] for f in indebidas}

    modulo = [t for f in validas if f["herramientas"] and (t := _tiempo_ms(f)) is not None]
    libre = [t for f in validas if not f["herramientas"] and (t := _tiempo_ms(f)) is not None]
    libre_filas = [f for f in validas if not f["herramientas"]]
    decision = [float(f["t_decision_ms"]) for f in validas if f.get("t_decision_ms") not in (None, "")]

    # Estabilidad: misma herramienta elegida en todas las repeticiones del caso.
    por_caso: dict[str, list[dict]] = defaultdict(list)
    for f in filas:
        por_caso[f["id"]].append(f)
    multi = {c: fs for c, fs in por_caso.items() if len(fs) > 1}
    estables = sum(1 for fs in multi.values()
                   if not any(f.get("error") for f in fs) and len({f["elegida"] for f in fs}) == 1)
    casos_siempre_ok = sum(1 for fs in por_caso.values()
                           if all(f["herramienta_ok"] and not f.get("error") for f in fs))

    trunc = [f for f in validas if f.get("truncada") not in (None, "")]
    resp_mod = [f for f in validas if f["herramientas"] and f.get("respuesta_modulo") not in (None, "")]

    return {
        "ejecuciones": n,
        "casos": len(por_caso),
        "errores": n - len(validas),
        "enrutado": _prop(enrutado, n),
        "enrutado_n": enrutado,
        "casos_siempre_ok": casos_siempre_ok,
        "argumentos": _prop(args_ok, len(con_args)),
        "argumentos_n": args_ok,
        "argumentos_d": len(con_args),
        "destructivas": len(destructivas),
        "destructivas_ids": sorted({f["id"] for f in destructivas}),
        "sin_agenda": sin_agenda,
        "indebidas_ejecuciones": len(indebidas),
        "indebidas_casos": len(casos_indebidos),
        "casos_trampa": len(casos_trampa),
        "indebidas_ids": sorted(casos_indebidos),
        "modulo_n": len(modulo),
        "modulo_p50_s": percentil(modulo, 0.5) / 1000,
        "modulo_p95_s": percentil(modulo, 0.95) / 1000,
        "libre_n": len(libre),
        "libre_p50_s": percentil(libre, 0.5) / 1000,
        "libre_p95_s": percentil(libre, 0.95) / 1000,
        "libre_max_s": (max(libre) / 1000) if libre else float("nan"),
        "libre_truncadas": sum(1 for f in libre_filas if _verdad(f.get("truncada"))),
        "decision_p50_s": percentil(decision, 0.5) / 1000,
        "estabilidad": _prop(estables, len(multi)),
        "estabilidad_n": estables,
        "estabilidad_d": len(multi),
        "truncadas": _prop(sum(1 for f in trunc if _verdad(f.get("truncada"))), len(trunc)),
        "respuesta_modulo": _prop(sum(1 for f in resp_mod if _verdad(f.get("respuesta_modulo"))),
                                  len(resp_mod)),
    }


def _verdad(v: Any) -> bool:
    return str(v).strip().lower() in {"1", "true", "sí", "si"}


# ── Informe ──────────────────────────────────────────────────────────────────

def _pct(x: float) -> str:
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.0%}"


def _s(x: float) -> str:
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.1f}"


def _cumple(ok: bool | None) -> str:
    return "–" if ok is None else ("sí" if ok else "**no**")


def _menor(x: float, umbral: float) -> bool | None:
    return None if math.isnan(x) else x <= umbral


def _mayor(x: float, umbral: float) -> bool | None:
    return None if math.isnan(x) else x >= umbral


def tabla_criterios(m: dict) -> list[str]:
    c = CRITERIOS
    filas = [
        ("Herramienta correcta (enrutado)", f"{_pct(m['enrutado'])} ({m['enrutado_n']}/{m['ejecuciones']} ejecuciones; "
         f"{m['casos_siempre_ok']}/{m['casos']} casos bien en todas las repeticiones)",
         f"≥ {c['enrutado']:.0%}", _mayor(m["enrutado"], c["enrutado"])),
        ("Argumentos correctos (con la herramienta bien elegida)",
         f"{_pct(m['argumentos'])} ({m['argumentos_n']}/{m['argumentos_d']})",
         f"≥ {c['argumentos']:.0%}", _mayor(m["argumentos"], c["argumentos"])),
        ("Acciones destructivas indebidas",
         f"{m['destructivas']}" + (f" ({', '.join(m['destructivas_ids'])})" if m["destructivas_ids"] else "")
         + (f"; {m['sin_agenda']} ejecuciones sin agenda comparable" if m["sin_agenda"] else ""),
         "0", (m["destructivas"] == 0) if not m["sin_agenda"] else (False if m["destructivas"] else None)),
        ("Llamadas indebidas en fuera de alcance y trampas",
         f"{m['indebidas_casos']} de {m['casos_trampa']} casos ({m['indebidas_ejecuciones']} ejecuciones)"
         + (f": {', '.join(m['indebidas_ids'])}" if m["indebidas_ids"] else ""),
         f"≤ {c['indebidas_casos']} caso (de los 5 del banco)", m["indebidas_casos"] <= c["indebidas_casos"]),
        ("Tiempo por petición a módulo, p50",
         f"{_s(m['modulo_p50_s'])} s (n = {m['modulo_n']})", f"≤ {c['modulo_p50_s']:.0f} s",
         _menor(m["modulo_p50_s"], c["modulo_p50_s"])),
        ("Tiempo por petición a módulo, p95", f"{_s(m['modulo_p95_s'])} s", f"≤ {c['modulo_p95_s']:.0f} s",
         _menor(m["modulo_p95_s"], c["modulo_p95_s"])),
        ("Conversación libre, tiempo total (p50 / p95 / máx.)",
         f"{_s(m['libre_p50_s'])} / {_s(m['libre_p95_s'])} / {_s(m['libre_max_s'])} s (n = {m['libre_n']}; "
         f"{m['libre_truncadas']} truncadas)",
         f"p95 ≤ {c['libre_p95_s']:.0f} s (pendiente de D2)", _menor(m["libre_p95_s"], c["libre_p95_s"])),
        ("Estabilidad entre repeticiones",
         f"{_pct(m['estabilidad'])} ({m['estabilidad_n']}/{m['estabilidad_d']} casos)",
         f"≥ {c['estabilidad']:.0%}", _mayor(m["estabilidad"], c["estabilidad"])),
        ("Respuestas truncadas (todas las ejecuciones)", _pct(m["truncadas"]), "informativo", None),
        ("Peticiones a módulo respondidas por el módulo (DA-11)", _pct(m["respuesta_modulo"]), "informativo", None),
        ("Decisión (primera llamada al LLM), p50", f"{_s(m['decision_p50_s'])} s", "informativo", None),
    ]
    out = ["| Métrica | Resultado | Criterio (propuesta) | ¿Cumple? |", "|---|---|---|---|"]
    out += [f"| {a} | {b} | {c_} | {_cumple(d)} |" for a, b, c_, d in filas]
    return out


def tabla_tipos(filas: list[dict], tipos: dict[str, str]) -> list[str]:
    out = ["| Tipo | Descripción | Casos | Ejec. | Enrutado | Argumentos | Destructivas indebidas "
           "| Llamadas indebidas | p50 (s) | Errores |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for tipo, desc in tipos.items():
        fs = [f for f in filas if f["tipo"] == tipo]
        if not fs:
            continue
        m = agregar(fs)
        tiempos = [t for f in fs if not f.get("error") and (t := _tiempo_ms(f)) is not None]
        out.append(
            f"| {tipo} | {desc} | {m['casos']} | {m['ejecuciones']} | {_pct(m['enrutado'])} "
            f"| {_pct(m['argumentos'])} ({m['argumentos_n']}/{m['argumentos_d']}) | {m['destructivas']} "
            f"| {m['indebidas_ejecuciones']} | {_s(percentil(tiempos, 0.5) / 1000)} | {m['errores']} |")
    return out


def motivos(f: dict) -> list[str]:
    r = []
    if f.get("error"):
        r.append(f"error de ejecución: {f['error']}")
    if not f["herramienta_ok"] and not f.get("error"):
        r.append(f"herramienta '{f['elegida'] or '(ninguna)'}' en vez de {f['esperadas']}")
    if f["argumentos_ok"] is False:
        r.append(f"argumentos ({f['checks_ok']}/{f['checks_total']} comprobaciones)")
    if f["destructiva_indebida"]:
        r.append(f"acción destructiva indebida: se pierde {', '.join(f['eventos_perdidos'])}")
    if f["llamada_indebida"]:
        r.append("llamada indebida")
    if f.get("nota"):
        r.append(f["nota"])
    return r


def resumen_md(filas: list[dict], tipos: dict[str, str], cabecera: dict) -> str:
    m = agregar(filas)
    lineas = [
        f"# Evaluación final · banco {cabecera.get('version_banco', '?')}", "",
        f"- Fecha de ejecución: {cabecera.get('fecha', '?')}",
        f"- Core: {cabecera.get('core', '?')} · modelo `{cabecera.get('modelo', '?')}` · "
        f"NUM_CTX={cabecera.get('num_ctx', '?')} · NUM_PREDICT={cabecera.get('num_predict', '?')}",
        f"- Fecha de referencia: {cabecera.get('referencia', '?')} · reloj del core: {cabecera.get('ahora_fijo', '?')}",
        f"- {m['casos']} casos × {cabecera.get('repeticiones', '?')} repeticiones = {m['ejecuciones']} ejecuciones"
        f" · errores de ejecución: {m['errores']}",
    ]
    if cabecera.get("commit"):
        lineas.append(f"- Commit: `{cabecera['commit']}`")
    if cabecera.get("aviso"):
        lineas += ["", f"> **Aviso:** {cabecera['aviso']}"]
    lineas += ["", "## Criterios de aceptación", ""] + tabla_criterios(m)
    lineas += ["", "Enrutado: la primera herramienta llamada (o ninguna) es una de las aceptables del caso; "
               "los errores de ejecución cuentan como fallo. Argumentos: resueltos con `fechas.py` contra la "
               "fecha de referencia (como `casos.a_canonico`). Destructiva indebida: desaparece de la agenda un "
               "evento que el caso no identifica de forma única. Tiempos: `t_total_ms` del registro de "
               "interacciones del core; \"petición a módulo\" = al menos una llamada a herramienta.", ""]
    lineas += ["## Por tipo de caso", ""] + tabla_tipos(filas, tipos)
    fallos = [(f, motivos(f)) for f in filas]
    fallos = [(f, r) for f, r in fallos if r]
    lineas += ["", f"## Fallos ({len(fallos)})", ""]
    if fallos:
        lineas += ["| Caso | Rep. | Tipo | Motivo | Argumentos del modelo |", "|---|---|---|---|---|"]
        for f, r in fallos:
            args = json.dumps(f["argumentos"], ensure_ascii=False).replace("|", "\\|")
            lineas.append(f"| {f['id']} | {f.get('repeticion', '')} | {f['tipo']} | {'; '.join(r)} | `{args}` |")
    else:
        lineas.append("Ninguno.")
    lineas += ["", "Revisión manual obligatoria (README del banco): E-X02, E-X05, E-M03 y E-S01. "
               "Detalle por ejecución en `crudo.csv`; configuración en `entorno.json`.", ""]
    return "\n".join(lineas)

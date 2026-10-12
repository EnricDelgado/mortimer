"""Spike de modelos: ¿qué modelo local elige bien la herramienta y extrae bien
los argumentos en castellano, y a qué coste?

Se ejecuta EN EL EQUIPO DE REFERENCIA (el servidor doméstico, CPU sin GPU),
contra el Ollama del docker compose:
    python benchmarks/benchmark_modelos.py --modelos qwen3:4b,qwen3:1.7b,granite4.1:3b

Opcionalmente, en el Mac de desarrollo, para comparar hardware.

Salida en benchmarks/resultados/<marca de tiempo>/:
    crudo.csv    una fila por (modelo, caso, repetición)
    resumen.md   tabla comparativa lista para el capítulo 4
    entorno.json hardware y versiones, para describir el montaje

Usa el mismo prompt de sistema que el core, con la fecha fijada en
casos.REFERENCIA para que las fechas relativas tengan solución única.
"""

from __future__ import annotations

import argparse
import csv
import json
import platform
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.orquestador import DIAS, PROMPT_SISTEMA, sellar  # noqa: E402
from casos import CASOS, HERRAMIENTAS, REFERENCIA, RESULTADOS_SIMULADOS  # noqa: E402

RE_FECHA = re.compile(r"(\d{4}-\d{2}-\d{2})")
RE_HORA = re.compile(r"[T ](\d{1,2}):(\d{2})")


def mensajes(frase: str) -> list[dict]:
    # Idéntico a lo que envía el core: sistema fijo + mensaje sellado con la fecha.
    return [{"role": "system", "content": PROMPT_SISTEMA},
            {"role": "user", "content": sellar(frase, REFERENCIA)}]


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


def llamar(http: httpx.Client, url: str, modelo: str, msgs: list[dict], think: bool | None,
           num_ctx: int) -> tuple[dict, float]:
    cuerpo = {
        "model": modelo, "stream": False, "options": {"temperature": 0, "num_ctx": num_ctx},
        "messages": msgs,
        "tools": HERRAMIENTAS,
    }
    if think is not None:
        cuerpo["think"] = think
    t = time.perf_counter()
    r = http.post(f"{url}/api/chat", json=cuerpo)
    if r.status_code == 400 and "think" in cuerpo:
        cuerpo.pop("think")
        t = time.perf_counter()
        r = http.post(f"{url}/api/chat", json=cuerpo)
    r.raise_for_status()
    return r.json(), (time.perf_counter() - t) * 1000


def memoria_modelo(http: httpx.Client, url: str, modelo: str) -> tuple[float | None, float | None]:
    """RAM total y parte en GPU (GB) que ocupa el modelo cargado, según /api/ps."""
    try:
        for m in http.get(f"{url}/api/ps").json().get("models", []):
            if m.get("name") == modelo or m.get("model") == modelo:
                return m.get("size", 0) / 1e9, m.get("size_vram", 0) / 1e9
    except Exception:
        pass
    return None, None


def percentil(datos: list[float], p: float) -> float:
    if not datos:
        return float("nan")
    s = sorted(datos)
    k = (len(s) - 1) * p
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def entorno(url: str, http: httpx.Client) -> dict:
    info = {"fecha": datetime.now().isoformat(timespec="seconds"),
            "plataforma": platform.platform(), "maquina": platform.machine(),
            "python": platform.python_version()}
    try:
        info["ollama"] = http.get(f"{url}/api/version").json().get("version")
    except Exception:
        info["ollama"] = None
    if sys.platform.startswith("linux"):
        try:
            for linea in Path("/proc/cpuinfo").read_text().splitlines():
                if linea.startswith("model name"):
                    info["cpu"] = linea.split(":", 1)[1].strip()
                    break
            info["nucleos"] = __import__("os").cpu_count()
            for linea in Path("/proc/meminfo").read_text().splitlines():
                if linea.startswith("MemTotal"):
                    info["ram_kb"] = int(linea.split()[1])
        except Exception:
            pass
    if sys.platform == "darwin":
        for clave, cmd in [("cpu", ["sysctl", "-n", "machdep.cpu.brand_string"]),
                           ("ram_bytes", ["sysctl", "-n", "hw.memsize"])]:
            try:
                info[clave] = subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
            except Exception:
                pass
    return info


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modelos", required=True, help="lista separada por comas")
    ap.add_argument("--url", default="http://localhost:11434")
    ap.add_argument("--repeticiones", type=int, default=3)
    ap.add_argument("--think", choices=["true", "false", "omitir"], default="false")
    ap.add_argument("--num-ctx", type=int, default=8192)
    ap.add_argument("--salida", default=str(Path(__file__).resolve().parent / "resultados"))
    a = ap.parse_args()

    think = None if a.think == "omitir" else a.think == "true"
    modelos = [m.strip() for m in a.modelos.split(",") if m.strip()]
    destino = Path(a.salida) / datetime.now().strftime("%Y%m%d-%H%M")
    destino.mkdir(parents=True, exist_ok=True)

    filas = []
    resumen = []
    with httpx.Client(timeout=300) as http:
        (destino / "entorno.json").write_text(json.dumps(entorno(a.url, http), indent=2, ensure_ascii=False))
        for modelo in modelos:
            print(f"\n== {modelo} ==")
            # Calentamiento: la primera llamada incluye la carga del modelo.
            _, ms_carga = llamar(http, a.url, modelo, mensajes("hola"), think, a.num_ctx)
            print(f"  carga + primera respuesta: {ms_carga / 1000:.1f} s")
            ram, vram = memoria_modelo(http, a.url, modelo)

            for rep in range(1, a.repeticiones + 1):
                for cid, frase, esperada, checks in CASOS:
                    try:
                        datos, ms = llamar(http, a.url, modelo, mensajes(frase), think, a.num_ctx)
                        error = ""
                    except Exception as e:
                        datos, ms, error = {}, float("nan"), str(e)
                    ms2, tok2 = 0.0, None
                    msg = datos.get("message", {})
                    llamadas = msg.get("tool_calls") or []
                    fn = llamadas[0]["function"] if llamadas else {}
                    nombre = fn.get("name")
                    args = fn.get("arguments") or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                    if nombre and not error:
                        # Segunda ronda: se devuelve un resultado simulado y se mide
                        # cuánto tarda el modelo en redactar la respuesta final.
                        try:
                            msgs2 = mensajes(frase) + [msg, {
                                "role": "tool", "tool_name": nombre,
                                "content": RESULTADOS_SIMULADOS.get(nombre, '{"ok": true}')}]
                            d2, ms2 = llamar(http, a.url, modelo, msgs2, think, a.num_ctx)
                            tok2 = d2.get("eval_count")
                        except Exception as e:
                            error = f"ronda 2: {e}"
                    ok_tool = nombre == esperada
                    pasados = [comprobar(args, c) for c in checks] if ok_tool else []
                    ok_args = ok_tool and all(pasados)
                    filas.append({
                        "modelo": modelo, "caso": cid, "rep": rep, "frase": frase,
                        "esperada": esperada or "", "elegida": nombre or "",
                        "ok_herramienta": int(ok_tool), "ok_argumentos": int(ok_args),
                        "checks_ok": sum(pasados), "checks_total": len(checks),
                        "argumentos": json.dumps(args, ensure_ascii=False),
                        "texto": (msg.get("content") or "")[:200].replace("\n", " "),
                        "ms_total": round(ms, 1),
                        "ms_ronda2": round(ms2, 1),
                        "ms_peticion": round(ms + ms2, 1),
                        "tokens_ronda2": tok2,
                        "ms_ollama": round(datos.get("total_duration", 0) / 1e6, 1),
                        "tokens_prompt": datos.get("prompt_eval_count"),
                        "ms_prompt": round(datos.get("prompt_eval_duration", 0) / 1e6, 1),
                        "tokens_salida": datos.get("eval_count"),
                        "ms_salida": round(datos.get("eval_duration", 0) / 1e6, 1),
                        "error": error,
                    })
                    marca = "✓" if ok_args or (ok_tool and not checks) else ("~" if ok_tool else "✗")
                    print(f"  r{rep} {cid} {marca} {nombre or '-':16} {ms:7.0f}+{ms2:6.0f} ms  {json.dumps(args, ensure_ascii=False)[:70]}")

            mias = [f for f in filas if f["modelo"] == modelo]
            con_tool = [f for f in mias if f["esperada"]]
            tiempos = [f["ms_peticion"] for f in mias if f["ms_peticion"] == f["ms_peticion"]]
            limite = sum(1 for t in tiempos if t <= 10_000) / len(tiempos) if tiempos else 0
            gen = [f["tokens_salida"] / (f["ms_salida"] / 1000) for f in mias
                   if f["tokens_salida"] and f["ms_salida"]]
            pre = [f["ms_prompt"] for f in mias if f["ms_prompt"]]
            resumen.append({
                "modelo": modelo,
                "herramienta": sum(f["ok_herramienta"] for f in mias) / len(mias),
                "argumentos": sum(f["ok_argumentos"] for f in con_tool) / max(len(con_tool), 1),
                "falsos_positivos": sum(1 for f in mias if not f["esperada"] and f["elegida"]),
                "media_s": statistics.mean(tiempos) / 1000 if tiempos else float("nan"),
                "mediana_s": statistics.median(tiempos) / 1000 if tiempos else float("nan"),
                "p95_s": percentil(tiempos, 0.95) / 1000,
                "ram_gb": ram, "vram_gb": vram,
                "carga_s": ms_carga / 1000,
                "rnf03": limite,
                "gen_tok_s": statistics.mean(gen) if gen else float("nan"),
                "prompt_ms": statistics.median(pre) if pre else float("nan"),
            })

    with open(destino / "crudo.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0]))
        w.writeheader()
        w.writerows(filas)

    n = len(CASOS)
    lineas = [
        f"# Spike de modelos — {datetime.now():%d/%m/%Y %H:%M}", "",
        f"{n} frases × {a.repeticiones} repeticiones por modelo. Temperatura 0, think={a.think}, num_ctx={a.num_ctx}. "
        f"Fecha de referencia: {DIAS[REFERENCIA.weekday()]} {REFERENCIA:%d/%m/%Y %H:%M}.", "",
        "| Modelo | Herramienta correcta | Argumentos correctos¹ | Llamadas indebidas² | Petición media (s)⁴ | Mediana (s) | P95 (s) | ≤ 10 s (RNF-03) | Prompt³ (ms) | Generación (tok/s) | Carga en frío (s) | RAM (GB) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in resumen:
        ram = f"{r['ram_gb']:.1f}" if r["ram_gb"] else "–"
        lineas.append(
            f"| {r['modelo']} | {r['herramienta']:.0%} | {r['argumentos']:.0%} | {r['falsos_positivos']} "
            f"| {r['media_s']:.2f} | {r['mediana_s']:.2f} | {r['p95_s']:.2f} | {r['rnf03']:.0%} | {r['prompt_ms']:.0f} "
            f"| {r['gen_tok_s']:.1f} | {r['carga_s']:.1f} | {ram} |"
        )
    lineas += ["",
               "¹ Sobre los casos que esperan herramienta: elige la correcta y todas las comprobaciones de argumentos pasan.",
               "² Veces que llamó a una herramienta en una frase fuera de alcance (N01–N03).",
               "³ Mediana del tiempo de procesado del prompt. Con la caché de prefijo de Ollama solo se procesa la parte nueva; en CPU es el término que más pesa.",
               "⁴ Petición completa: elección de herramienta + redacción de la respuesta final con un resultado simulado. Es el tiempo que percibe el usuario, sin contar el módulo.",
               "", "Detalle por caso en `crudo.csv`; hardware en `entorno.json`."]
    (destino / "resumen.md").write_text("\n".join(lineas), encoding="utf-8")
    print("\n" + "\n".join(lineas))
    print(f"\nResultados en {destino}")


if __name__ == "__main__":
    main()

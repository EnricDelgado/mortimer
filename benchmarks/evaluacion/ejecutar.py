"""Ejecutor del banco de evaluación final (fase 4, corrección funcional y rendimiento).

Se ejecuta EN EL SERVIDOR contra el core de staging, con el reloj fijado en la
fecha de referencia del banco (MORTIMER_AHORA=2026-10-29T18:00 en el .env):

    .venv/bin/python benchmarks/evaluacion/ejecutar.py --core http://localhost:8100 \\
        --repeticiones 3 --salida /srv/mortimer-staging/eval --commit $(git rev-parse --short HEAD)

Para cada caso de banco.py y cada repetición:
  1. Restaura la agenda inicial del Calendario (POST /m/calendario/api/reset).
  2. Lee la agenda (GET /m/calendario/api/eventos).
  3. Envía la frase al core (POST /api/chat, conversación nueva).
  4. Recoge la interacción registrada (GET /api/interacciones?desde_id=...).
  5. Vuelve a leer la agenda y puntúa con metricas.py.

Salida en --salida: crudo.csv (una fila por ejecución, se escribe sobre la
marcha), resumen.md (métricas de la tabla de criterios del README, por tipo
de caso y lista de fallos) y entorno.json (modelo, NUM_CTX, NUM_PREDICT,
commit...).

Solo necesita httpx (está en core/requirements.txt). No modifica banco.py.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
sys.path.insert(0, str(AQUI.parent))   # benchmarks/: casos.a_canonico

import banco      # noqa: E402
import metricas   # noqa: E402

COLUMNAS = [
    "id", "tipo", "repeticion", "frase", "esperadas", "elegida", "herramientas",
    "argumentos", "argumentos_canonicos", "herramienta_ok", "argumentos_ok", "checks_ok",
    "checks_total", "eventos_antes", "eventos_despues", "eventos_perdidos",
    "destructiva_indebida", "llamada_indebida", "respuesta", "respuesta_modulo",
    "truncada", "rondas_llm", "t_decision_ms", "t_llm_ms", "t_modulos_ms", "t_total_ms",
    "t_cliente_ms", "interaccion_id", "conversacion", "error", "nota",
]


class Abortar(Exception):
    """Error de configuración: no tiene sentido seguir."""


def hash_banco() -> str:
    return hashlib.sha256((AQUI / "banco.py").read_bytes()).hexdigest()


def a_instante(valor: Any, zona: str = "Europe/Madrid") -> datetime | None:
    if not valor:
        return None
    try:
        dt = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo(zona))


def comprobar_reloj(health: dict, referencia: datetime) -> tuple[bool, str]:
    """El core tiene que tener el reloj fijo (DEV-10) en la fecha del banco."""
    if "ahora_fijo" not in health:
        return False, "el core no informa de 'ahora_fijo' en /health (¿versión anterior a DEV-10?)"
    fijo = a_instante(health.get("ahora_fijo"))
    if fijo is None:
        return False, ("el core usa la hora real (ahora_fijo = null). Pon MORTIMER_AHORA="
                       f"{referencia:%Y-%m-%dT%H:%M} en el .env de staging y reinicia")
    if fijo != referencia:
        return False, f"ahora_fijo = {health.get('ahora_fijo')}, distinto de la referencia {referencia.isoformat()}"
    return True, f"reloj fijo en {health.get('ahora_fijo')}"


def leer_env(ruta: Path) -> dict[str, str]:
    valores: dict[str, str] = {}
    try:
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            k, v = linea.split("=", 1)
            valores[k.strip()] = v.split(" #", 1)[0].strip().strip('"').strip("'")
    except OSError:
        pass
    return valores


def parametro(nombre: str, health: dict, env_file: Path | None) -> tuple[Any, str]:
    """NUM_CTX / NUM_PREDICT: del /health si lo publica, si no del entorno o del .env."""
    clave = nombre.lower()
    if health.get(clave) is not None:
        return health[clave], "/health"
    if os.environ.get(nombre):
        return os.environ[nombre], "entorno"
    if env_file and leer_env(env_file).get(nombre):
        return leer_env(env_file)[nombre], str(env_file)
    return None, "desconocido (no está en /health ni en el .env: se usa el valor por defecto del compose)"


class Cliente:
    """Acceso HTTP al core y, a través de su proxy, al módulo Calendario."""

    def __init__(self, core: str, prefijo: str = "/m/calendario", timeout: float = 300,
                 transport: httpx.BaseTransport | None = None):
        self.http = httpx.Client(base_url=core.rstrip("/"), timeout=timeout, transport=transport)
        self.prefijo = "/" + prefijo.strip("/")

    def cerrar(self) -> None:
        self.http.close()

    def health(self) -> dict:
        r = self.http.get("/health", timeout=15)
        r.raise_for_status()
        return r.json()

    def modulos(self) -> list[dict]:
        try:
            return self.http.get("/api/modulos", timeout=15).json()
        except Exception:
            return []

    def reset(self, eventos: list[dict]) -> None:
        r = self.http.post(f"{self.prefijo}/api/reset", json={"eventos": eventos}, timeout=30)
        if r.status_code == 403:
            raise Abortar("el Calendario rechaza /api/reset (403): falta CALENDARIO_PERMITIR_RESET=1 "
                          "en el servicio calendario-stg")
        if r.status_code == 404:
            raise Abortar(f"{self.prefijo}/api/reset no existe (404): ¿está el Calendario en MODULOS y el proxy en el core?")
        r.raise_for_status()

    def agenda(self, referencia: datetime) -> list[dict]:
        desde = (referencia - timedelta(days=400)).date().isoformat()
        hasta = (referencia + timedelta(days=400)).date().isoformat()
        r = self.http.get(f"{self.prefijo}/api/eventos",
                          params={"desde": desde, "hasta": hasta, "incluir_cancelados": 0}, timeout=30)
        r.raise_for_status()
        datos = r.json()
        if isinstance(datos, dict):   # tolera {"eventos": [...]}
            datos = datos.get("eventos", [])
        return list(datos)

    def ultimo_id(self) -> int:
        r = self.http.get("/api/interacciones", params={"limite": 1}, timeout=15)
        r.raise_for_status()
        filas = r.json()
        return int(filas[0]["id"]) if filas else 0

    def chat(self, texto: str) -> dict:
        r = self.http.post("/api/chat", json={"texto": texto})
        r.raise_for_status()
        return r.json()

    def interaccion(self, desde_id: int, iid: int | None, texto: str) -> dict | None:
        r = self.http.get("/api/interacciones", params={"desde_id": desde_id, "limite": 50}, timeout=15)
        r.raise_for_status()
        filas = r.json()
        if iid is not None:
            for f in filas:
                if f.get("id") == iid:
                    return f
        nuevas = [f for f in filas if int(f.get("id", 0)) > desde_id and f.get("peticion") == texto]
        return max(nuevas, key=lambda f: f["id"]) if nuevas else None


def _celda(v: Any) -> Any:
    if isinstance(v, bool):
        return int(v)
    if v is None:
        return ""
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    return v


def ejecutar_caso(cli: Cliente, caso: tuple, rep: int, agenda: list[dict], referencia: datetime,
                  canonizar: Callable | None, desde_id: int) -> tuple[dict, int]:
    cid, tipo, frase, esperado, _ = caso
    error = ""
    antes = despues = None
    respuesta: dict = {}
    inter: dict | None = None
    t_cliente = None
    try:
        cli.reset(agenda)
        antes = cli.agenda(referencia)
        t0 = time.perf_counter()
        respuesta = cli.chat(frase)
        t_cliente = round((time.perf_counter() - t0) * 1000, 1)
        inter = cli.interaccion(desde_id, respuesta.get("interaccion_id"), frase)
        if inter is None:
            error = "no se encuentra la interacción en /api/interacciones"
        elif inter.get("error"):
            error = f"core: {inter['error']}"
        despues = cli.agenda(referencia)
    except Abortar:
        raise
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    llamadas = (inter or {}).get("herramientas")
    if llamadas is None:
        llamadas = respuesta.get("herramientas") or []
    p = metricas.puntuar(caso, llamadas, canonizar, referencia.date(), antes, despues)
    inter = inter or {}
    fila = {
        **p, "repeticion": rep, "frase": frase,
        "eventos_antes": len(antes) if antes is not None else "",
        "eventos_despues": len(despues) if despues is not None else "",
        "respuesta": inter.get("respuesta", respuesta.get("texto", "")),
        "respuesta_modulo": inter.get("respuesta_modulo"),
        "truncada": inter.get("truncada", respuesta.get("truncada")),
        "rondas_llm": inter.get("rondas_llm"),
        "t_decision_ms": inter.get("t_decision_ms"),
        "t_llm_ms": inter.get("t_llm_ms"),
        "t_modulos_ms": inter.get("t_modulos_ms"),
        "t_total_ms": inter.get("t_total_ms"),
        "t_cliente_ms": t_cliente,
        "interaccion_id": inter.get("id", respuesta.get("interaccion_id")),
        "conversacion": respuesta.get("conversacion", ""),
        "error": error,
    }
    nuevo_desde = int(fila["interaccion_id"]) if fila["interaccion_id"] not in (None, "") else desde_id
    return fila, max(desde_id, nuevo_desde)


def ejecutar(core: str, repeticiones: int, salida: Path, *, commit: str | None = None,
             forzar: bool = False, sobrescribir: bool = False, calentar: bool = True,
             prefijo: str = "/m/calendario", env_file: Path | None = None,
             casos: list[tuple] | None = None, agenda: list[dict] | None = None,
             referencia: datetime | None = None, tipos: dict[str, str] | None = None,
             canonizar: Callable | None = None, transport: httpx.BaseTransport | None = None,
             timeout: float = 300, log: Callable[[str], None] = print) -> Path:
    casos = banco.CASOS if casos is None else casos
    agenda = banco.AGENDA_INICIAL if agenda is None else agenda
    referencia = banco.REFERENCIA if referencia is None else referencia
    tipos = banco.TIPOS if tipos is None else tipos
    if canonizar is None:
        from casos import a_canonico
        canonizar = a_canonico

    salida = Path(salida)
    crudo = salida / "crudo.csv"
    if crudo.exists() and not sobrescribir:
        raise Abortar(f"{crudo} ya existe: usa otra --salida o --sobrescribir")
    salida.mkdir(parents=True, exist_ok=True)

    hash_inicio = hash_banco()
    cli = Cliente(core, prefijo, timeout, transport)
    inicio = datetime.now()
    t0 = time.perf_counter()
    try:
        try:
            health = cli.health()
        except Exception as e:
            raise Abortar(f"el core no responde en {core}/health: {e}")
        ok, msg = comprobar_reloj(health, referencia)
        aviso = ""
        if not ok:
            if not forzar:
                raise Abortar(msg + ". Usa --forzar para seguir igualmente.")
            aviso = (f"{msg}. Ejecutado con --forzar: las fechas esperadas del banco NO son válidas "
                     "y los argumentos de fecha saldrán mal.")
            log(f"AVISO: {aviso}")
        else:
            log(f"Core {core}: modelo {health.get('modelo')}, {msg}")

        num_ctx, fuente_ctx = parametro("NUM_CTX", health, env_file)
        num_predict, fuente_pred = parametro("NUM_PREDICT", health, env_file)
        entorno = {
            "fecha": inicio.isoformat(timespec="seconds"),
            "core": core,
            "modelo": health.get("modelo"),
            "num_ctx": num_ctx, "num_ctx_fuente": fuente_ctx,
            "num_predict": num_predict, "num_predict_fuente": fuente_pred,
            "ahora_fijo": health.get("ahora_fijo"),
            "referencia": referencia.isoformat(),
            "commit": commit,
            "version_banco": banco.VERSION,
            "sha256_banco": hash_inicio,
            "casos": len(casos),
            "repeticiones": repeticiones,
            "forzado": bool(aviso),
            "health": health,
            "modulos": cli.modulos(),
            "plataforma": platform.platform(),
            "python": platform.python_version(),
        }
        (salida / "entorno.json").write_text(json.dumps(entorno, indent=2, ensure_ascii=False), encoding="utf-8")

        cli.reset(agenda)   # detecta pronto un 403/404 del reset
        if calentar:
            # La primera petición puede incluir la carga del modelo: no se mide.
            try:
                cli.chat("hola")
                log("Calentamiento hecho (no cuenta).")
            except Exception as e:
                log(f"Calentamiento fallido: {e}")
        desde_id = cli.ultimo_id()

        filas: list[dict] = []
        with open(crudo, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNAS, extrasaction="ignore")
            w.writeheader()
            total = repeticiones * len(casos)
            for rep in range(1, repeticiones + 1):
                for caso in casos:
                    fila, desde_id = ejecutar_caso(cli, caso, rep, agenda, referencia, canonizar, desde_id)
                    filas.append(fila)
                    w.writerow({k: _celda(fila.get(k)) for k in COLUMNAS})
                    fh.flush()
                    marca = "✗" if metricas.motivos(fila) else "✓"
                    t = fila["t_total_ms"] or fila["t_cliente_ms"] or 0
                    log(f"  [{len(filas):3d}/{total}] r{rep} {fila['id']:6} {marca} "
                        f"{fila['elegida'] or '-':16} {float(t) / 1000:6.1f} s  "
                        f"{'; '.join(metricas.motivos(fila))[:90]}")

        entorno["duracion_s"] = round(time.perf_counter() - t0, 1)
        (salida / "entorno.json").write_text(json.dumps(entorno, indent=2, ensure_ascii=False), encoding="utf-8")
        cabecera = {**entorno, "fecha": entorno["fecha"], "aviso": aviso}
        (salida / "resumen.md").write_text(metricas.resumen_md(filas, tipos, cabecera), encoding="utf-8")
    finally:
        cli.cerrar()

    if hash_banco() != hash_inicio:
        log("AVISO: banco.py ha cambiado durante la ejecución.")
    log(f"Resultados en {salida} ({time.perf_counter() - t0:.0f} s)")
    return salida


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--core", required=True, help="URL del core, p. ej. http://localhost:8100")
    ap.add_argument("--repeticiones", type=int, default=3)
    ap.add_argument("--salida", required=True, help="carpeta de resultados (se crea si no existe)")
    ap.add_argument("--commit", help="hash del commit desplegado, para entorno.json")
    ap.add_argument("--forzar", action="store_true",
                    help="sigue aunque el reloj del core no esté en la fecha de referencia (resultados no válidos)")
    ap.add_argument("--sobrescribir", action="store_true", help="permite reutilizar una carpeta con crudo.csv")
    ap.add_argument("--sin-calentamiento", action="store_true", help="no envía el 'hola' previo")
    ap.add_argument("--prefijo-calendario", default="/m/calendario",
                    help="ruta del módulo Calendario a través del proxy del core")
    ap.add_argument("--env-file", default=".env",
                    help="de dónde leer NUM_CTX y NUM_PREDICT si /health no los publica (por defecto ./.env)")
    ap.add_argument("--timeout", type=float, default=300, help="segundos máximos por petición al chat")
    a = ap.parse_args(argv)
    try:
        ejecutar(a.core, a.repeticiones, Path(a.salida), commit=a.commit, forzar=a.forzar,
                 sobrescribir=a.sobrescribir, calentar=not a.sin_calentamiento,
                 prefijo=a.prefijo_calendario, env_file=Path(a.env_file), timeout=a.timeout)
    except Abortar as e:
        print(f"ABORTADO: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

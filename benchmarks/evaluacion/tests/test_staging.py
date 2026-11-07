"""Pruebas de DEV-11: docker-compose.staging.yml y scripts/smoke.sh.

No arrancan Docker: leen el YAML y ejecutan smoke.sh contra un core falso
(un http.server local) para comprobar que distingue el éxito del fallo.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

# Sin PyYAML solo se saltan las pruebas del compose, una a una y a la vista
# (QA D-03); las de smoke.sh se ejecutan igual.
try:
    import yaml
except ImportError:  # pragma: no cover - depende del venv
    yaml = None
requiere_yaml = pytest.mark.skipif(yaml is None, reason="falta PyYAML: pip install -r benchmarks/evaluacion/tests/requirements.txt")

RAIZ = Path(__file__).resolve().parents[3]
COMPOSE = RAIZ / "docker-compose.staging.yml"
SMOKE = RAIZ / "scripts" / "smoke.sh"


@pytest.fixture(scope="module")
def compose() -> dict:
    if yaml is None:
        pytest.skip("falta PyYAML: pip install -r benchmarks/evaluacion/tests/requirements.txt")
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_servicios(compose):
    assert compose["name"] == "mortimer-staging"
    assert set(compose["services"]) == {"core", "prueba-stg", "calendario-stg"}
    assert compose["services"]["core"]["build"] == "./core"
    assert compose["services"]["prueba-stg"]["build"] == "./modules/prueba"
    assert compose["services"]["calendario-stg"]["build"] == "./modules/calendario"
    assert set(compose["services"]["core"]["depends_on"]) == {"prueba-stg", "calendario-stg"}


def test_red_externa_de_produccion(compose):
    assert compose["networks"]["prod"] == {"external": True, "name": "mortimer_default"}
    assert set(compose["services"]["core"]["networks"]) == {"default", "prod"}
    # Los módulos de staging solo están en la red propia.
    for m in ("prueba-stg", "calendario-stg"):
        assert "networks" not in compose["services"][m]


def test_solo_el_core_publica_puerto_8100(compose):
    publicados = {n: s.get("ports") for n, s in compose["services"].items() if s.get("ports")}
    assert publicados == {"core": ["8100:8000"]}


def test_variables_del_core(compose):
    env = compose["services"]["core"]["environment"]
    modulos = [m.strip() for m in env["MODULOS"].split(",")]
    assert modulos == ["http://prueba-stg:8001", "http://calendario-stg:8001"]
    assert env["OLLAMA_URL"] == "http://ollama:11434"
    assert env["MORTIMER_AHORA"] == "${MORTIMER_AHORA:-}"
    assert env["NUM_PREDICT"] == "${NUM_PREDICT:-160}"
    assert compose["services"]["core"]["volumes"] == ["./data/core:/data"]


def test_variables_del_calendario(compose):
    cal = compose["services"]["calendario-stg"]
    env = cal["environment"]
    assert env["ALLOWED_HOSTS"] == "calendario-stg:*"
    assert env["CALENDARIO_PERMITIR_RESET"] == "1"
    assert env["MORTIMER_AHORA"] == "${MORTIMER_AHORA:-}"
    assert env["ZONA_HORARIA"] == "Europe/Madrid"
    assert cal["volumes"] == ["./data/calendario:/data"]
    assert compose["services"]["prueba-stg"]["environment"]["ALLOWED_HOSTS"] == "prueba-stg:*"


@requiere_yaml
def test_produccion_no_activa_el_reset():
    prod = yaml.safe_load((RAIZ / "docker-compose.yml").read_text(encoding="utf-8"))
    for servicio in prod["services"].values():
        assert "CALENDARIO_PERMITIR_RESET" not in (servicio.get("environment") or {})


def test_smoke_sintaxis():
    r = subprocess.run(["bash", "-n", str(SMOKE)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    texto = SMOKE.read_text(encoding="utf-8")
    assert "/m/calendario/calendario.ics" in texto and "BEGIN:VCALENDAR" in texto
    assert '"calendario"' in texto


# ── smoke.sh contra un core falso ────────────────────────────────────────────

def _servidor(modulos: list[dict], ics: str, fallar: frozenset[str] = frozenset()):
    class Manejador(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, datos, codigo=200):
            cuerpo = json.dumps(datos, separators=(",", ":")).encode()
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def do_GET(self):
            if self.path == "/health":
                return self._json({"estado": "ok", "modelo": "falso", "ahora_fijo": None})
            if self.path == "/api/modulos":
                if "modulos" in fallar:
                    return self._json({"detail": "Internal Server Error"}, 500)
                return self._json(modulos)
            if self.path.startswith("/api/interacciones"):
                if "interacciones" in fallar:
                    return self._json({"detail": "Internal Server Error"}, 500)
                return self._json([{"id": 1, "error": None}])
            if self.path == "/m/calendario/calendario.ics" and ics:
                cuerpo = ics.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/calendar; charset=utf-8")
                self.send_header("Content-Length", str(len(cuerpo)))
                self.end_headers()
                return self.wfile.write(cuerpo)
            self._json({"detail": "Not Found"}, 404)

        def do_POST(self):
            texto = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["texto"]
            if "libre" in fallar and "capitalismo" in texto:
                return self._json({"detail": "Internal Server Error"}, 500)
            herr = [{"modulo": "prueba", "herramienta": "dime_la_hora", "argumentos": {}, "ok": True}] \
                if "hora" in texto else []
            self._json({"texto": "ok", "conversacion": "c", "herramientas": herr, "interaccion_id": 1})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Manejador)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _smoke(modulos, ics, fallar=frozenset()):
    srv = _servidor(modulos, ics, frozenset(fallar))
    try:
        return subprocess.run(["bash", str(SMOKE), f"http://127.0.0.1:{srv.server_port}"],
                              capture_output=True, text=True, timeout=90)
    finally:
        srv.shutdown()


requiere_curl = pytest.mark.skipif(not (shutil.which("curl") and shutil.which("python3")),
                                   reason="smoke.sh necesita curl y python3")
MODULOS_OK = [{"nombre": "prueba", "estado": "ok"}, {"nombre": "calendario", "estado": "ok"}]
ICS = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Mortimer//ES\r\nEND:VCALENDAR\r\n"


@requiere_curl
def test_smoke_pasa_con_calendario():
    r = _smoke(MODULOS_OK, ICS)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "módulo calendario registrado y ok" in r.stdout
    assert "calendario.ics por el proxy" in r.stdout
    assert r.stdout.strip().endswith("OK")


@requiere_curl
def test_smoke_falla_sin_calendario():
    r = _smoke([{"nombre": "prueba", "estado": "ok"}], "")
    assert r.returncode == 1
    assert "módulo calendario: ausente" in r.stdout
    assert "calendario.ics no empieza por BEGIN:VCALENDAR" in r.stdout
    assert "2 fallo(s)" in r.stdout


@requiere_curl
def test_smoke_falla_con_calendario_caido():
    r = _smoke([{"nombre": "prueba", "estado": "ok"}, {"nombre": "calendario", "estado": "error"}], ICS)
    assert r.returncode == 1
    assert "módulo calendario: error" in r.stdout


# QA D-01: tres comprobaciones daban verde aunque la petición HTTP fallara.
@requiere_curl
@pytest.mark.parametrize("fallo,mensaje,verde", [
    ("modulos", "api/modulos no responde", "módulos ok"),
    ("libre", "conversación libre sin respuesta", "conversación libre en"),
    ("interacciones", "api/interacciones no responde", "interacciones sin errores"),
])
def test_smoke_sin_falsos_verdes(fallo, mensaje, verde):
    r = _smoke(MODULOS_OK, ICS, {fallo})
    assert r.returncode == 1, r.stdout
    assert mensaje in r.stdout
    assert verde not in r.stdout

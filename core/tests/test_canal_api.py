"""La API registra el canal por el que llega la petición (canal de Telegram)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Config
from app.llm import RespuestaLLM
from app.main import crear_app


class LLMFijo:
    modelo = "fijo"

    async def chat(self, mensajes, herramientas):
        return RespuestaLLM("Hola.")


def cliente(tmp_path):
    return TestClient(crear_app(Config(data_dir=tmp_path), llm=LLMFijo(), destinos=[]))


def test_canal_telegram_queda_registrado(tmp_path):
    with cliente(tmp_path) as c:
        r = c.post("/api/chat", json={"texto": "hola", "conversacion": "tg-1", "canal": "telegram"})
        assert r.status_code == 200 and r.json()["texto"] == "Hola."
        assert c.get("/api/interacciones").json()[0]["canal"] == "telegram"


def test_canal_por_defecto_es_api(tmp_path):
    with cliente(tmp_path) as c:
        c.post("/api/chat", json={"texto": "hola"})
        assert c.get("/api/interacciones").json()[0]["canal"] == "api"


def test_canal_desconocido_se_rechaza(tmp_path):
    with cliente(tmp_path) as c:
        assert c.post("/api/chat", json={"texto": "hola", "canal": "sms"}).status_code == 422

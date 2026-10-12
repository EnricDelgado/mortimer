"""Core de Mortimer: chat web, API y registro de módulos."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .config import Config
from .llm import OllamaLLM
from .memoria import Memoria
from .orquestador import Orquestador
from .registro import Registro

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
BASE = Path(__file__).resolve().parent
plantillas = Jinja2Templates(directory=str(BASE.parent / "templates"))


class Peticion(BaseModel):
    texto: str
    conversacion: str | None = None


def crear_app(config: Config | None = None, llm=None, destinos=None) -> FastAPI:
    config = config or Config()

    @asynccontextmanager
    async def ciclo(app: FastAPI):
        registro = Registro(destinos if destinos is not None else config.modulos)
        await registro.actualizar()
        modelo = llm or OllamaLLM(config.ollama_url, config.modelo, config.think, config.num_ctx)
        app.state.registro = registro
        app.state.memoria = Memoria(config.data_dir / "mortimer.db")
        app.state.orq = Orquestador(
            modelo, registro, app.state.memoria, config.zona_horaria,
            config.max_rondas_herramientas, config.historial_mensajes,
        )
        yield
        if hasattr(modelo, "cerrar"):
            await modelo.cerrar()

    app = FastAPI(title="Mortimer core", version="0.1.0", lifespan=ciclo)
    # htmx se sirve desde aquí, no desde un CDN: el chat funciona sin internet (RNF-02).
    app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

    @app.get("/health")
    async def health():
        return {"estado": "ok", "modelo": config.modelo,
                "herramientas": list(app.state.registro.herramientas)}

    @app.post("/api/chat")
    async def api_chat(p: Peticion):
        r = await app.state.orq.responder(p.texto, p.conversacion, canal="api")
        return asdict(r)

    @app.get("/api/modulos")
    async def api_modulos():
        return [asdict(m) for m in app.state.registro.modulos]

    @app.post("/api/modulos/recargar")
    async def recargar():
        await app.state.registro.actualizar()
        return [asdict(m) for m in app.state.registro.modulos]

    @app.get("/api/interacciones")
    async def interacciones(limite: int = 50):
        return app.state.memoria.interacciones(limite)

    @app.get("/", response_class=HTMLResponse)
    async def chat(request: Request):
        return plantillas.TemplateResponse(request, "chat.html", {
            "modelo": config.modelo, "modulos": app.state.registro.modulos,
        })

    @app.post("/chat", response_class=HTMLResponse)
    async def chat_htmx(request: Request, texto: str = Form(...), conversacion: str = Form("")):
        r = await app.state.orq.responder(texto, conversacion or None, canal="web")
        return plantillas.TemplateResponse(request, "_turno.html", {"texto": texto, "r": r})

    return app


app = crear_app()

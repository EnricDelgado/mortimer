"""Core de Mortimer: frontend, API y registro de módulos.

Frontend del core (Jinja2, sin dependencias externas, DA-04):
  /                      mapa de módulos instalados
  /conversaciones        registro de conversaciones, consultable y borrable
  /conversaciones/<id>   una conversación con las herramientas que invocó
El chat con Mortimer es un widget flotante (static/chat-flotante.js) que se
incluye igual en estas páginas y en los frontends de los módulos: Mortimer es
uno solo y se le habla siempre de la misma forma.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .config import Config
from .llm import OllamaLLM
from .memoria import Memoria
from .orquestador import Orquestador
from .proxy import METODOS, Proxy
from .registro import Registro

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
BASE = Path(__file__).resolve().parent
plantillas = Jinja2Templates(directory=str(BASE.parent / "templates"))


class Peticion(BaseModel):
    texto: str
    conversacion: str | None = None
    # El chat flotante manda "web"; el bot de Telegram, "telegram"; la evaluación y smoke.sh, el valor por defecto.
    canal: Literal["api", "web", "telegram"] = "api"


def _fecha_local(zona: str):
    tz = ZoneInfo(zona)

    def filtro(iso: str | None) -> str:
        if not iso:
            return ""
        try:
            return datetime.fromisoformat(iso).astimezone(tz).strftime("%d/%m/%Y %H:%M")
        except ValueError:
            return iso
    return filtro


def _primera_frase(texto: str) -> str:
    texto = " ".join((texto or "").split())
    for fin in (". ", "\n"):
        if fin in texto:
            return texto.split(fin, 1)[0].rstrip(".") + "."
    return texto


def mapa_modulos(registro: Registro, uso: dict) -> list[dict]:
    """Lo que muestra la portada: cada módulo con sus herramientas y su uso."""
    res = []
    for m in registro.modulos:
        herramientas = []
        for nombre in m.herramientas:
            h = registro.herramientas.get(nombre)
            if h is None:
                continue
            props = (h.esquema or {}).get("properties") or {}
            herramientas.append({
                "nombre": nombre,
                "resumen": _primera_frase(h.descripcion),
                "parametros": list(props),
                "requeridos": h.requeridos,
            })
        res.append({
            "nombre": m.nombre, "version": m.version, "estado": m.estado, "error": m.error,
            "descripcion": m.descripcion, "herramientas": herramientas,
            "frontend": bool(m.url) and m.estado == "ok",
            "uso": uso.get(m.nombre, {"peticiones": 0, "ultima": None}),
        })
    return res


def crear_app(config: Config | None = None, llm=None, destinos=None) -> FastAPI:
    config = config or Config()

    @asynccontextmanager
    async def ciclo(app: FastAPI):
        registro = Registro(destinos if destinos is not None else config.modulos)
        await registro.actualizar()
        # Los módulos pueden tardar unos segundos más que el core en arrancar
        # (QA C-01): se reintenta unas veces antes de dejarlos en error.
        for _ in range(config.reintentos_arranque):
            if not any(m.estado == "error" for m in registro.modulos):
                break
            await asyncio.sleep(2)
            await registro.actualizar()
        app.state.proxy = Proxy(registro)
        modelo = llm or OllamaLLM(config.ollama_url, config.modelo, config.think, config.num_ctx,
                                  config.num_predict)
        app.state.registro = registro
        app.state.memoria = Memoria(config.data_dir / "mortimer.db")
        app.state.orq = Orquestador(
            modelo, registro, app.state.memoria, config.zona_horaria,
            config.max_rondas_herramientas, config.historial_mensajes, config.ahora_fijo,
        )
        yield
        await app.state.proxy.cerrar()
        if hasattr(modelo, "cerrar"):
            await modelo.cerrar()

    plantillas.env.filters["local"] = _fecha_local(config.zona_horaria)
    app = FastAPI(title="Mortimer core", version="0.1.0", lifespan=ciclo)
    # htmx se sirve desde aquí, no desde un CDN: el chat funciona sin internet (RNF-02).
    app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

    @app.get("/health")
    async def health():
        fijo = app.state.orq.ahora_fijo
        return {"estado": "ok", "modelo": config.modelo,
                "herramientas": list(app.state.registro.herramientas),
                "ahora_fijo": fijo.isoformat() if fijo else None}

    @app.post("/api/chat")
    async def api_chat(p: Peticion):
        r = await app.state.orq.responder(p.texto, p.conversacion, canal=p.canal)
        return asdict(r)

    @app.get("/api/modulos")
    async def api_modulos():
        await app.state.registro.reintentar_caidos()
        return [asdict(m) for m in app.state.registro.modulos]

    @app.post("/api/modulos/recargar")
    async def recargar():
        await app.state.registro.actualizar()
        return [asdict(m) for m in app.state.registro.modulos]

    @app.get("/api/interacciones")
    async def interacciones(limite: int = 50, desde_id: int | None = None):
        return app.state.memoria.interacciones(limite, desde_id)

    @app.api_route("/m/{modulo}/{ruta:path}", methods=METODOS)
    async def proxy_modulo(request: Request, modulo: str, ruta: str):
        return await app.state.proxy.reenviar(request, modulo, ruta)

    # ── Registro de conversaciones (API) ──────────────────────────────────
    @app.get("/api/conversaciones")
    async def api_conversaciones(limite: int = 200):
        return app.state.memoria.conversaciones(limite)

    @app.get("/api/conversaciones/{conversacion}")
    async def api_conversacion(conversacion: str):
        turnos = app.state.memoria.conversacion(conversacion)
        if not turnos:
            raise HTTPException(404, "Conversación no encontrada")
        return {"conversacion": conversacion, "turnos": turnos}

    @app.delete("/api/conversaciones/{conversacion}")
    async def api_borrar_conversacion(conversacion: str):
        return {"borrados": app.state.memoria.borrar_conversacion(conversacion)}

    @app.delete("/api/conversaciones")
    async def api_borrar_todo():
        return {"borrados": app.state.memoria.borrar_todo()}

    # ── Frontend del core ─────────────────────────────────────────────────
    # Enlaces relativos (como en los módulos): `raiz` lleva a la raíz del core.
    def pagina(request: Request, plantilla: str, raiz: str, **datos):
        return plantillas.TemplateResponse(request, plantilla, {
            "raiz": raiz, "modelo": config.modelo, **datos,
        })

    @app.get("/", response_class=HTMLResponse)
    async def inicio(request: Request):
        await app.state.registro.reintentar_caidos()
        reg = app.state.registro
        return pagina(request, "inicio.html", "", seccion="modulos",
                      modulos=mapa_modulos(reg, app.state.memoria.uso_modulos()),
                      n_herramientas=len(reg.herramientas))

    @app.post("/modulos/recargar")
    async def recargar_html():
        await app.state.registro.actualizar()
        return RedirectResponse("../", status_code=303)

    @app.get("/conversaciones", response_class=HTMLResponse)
    async def conversaciones(request: Request):
        return pagina(request, "conversaciones.html", "", seccion="conversaciones",
                      conversaciones=app.state.memoria.conversaciones())

    @app.get("/conversaciones/{conversacion}", response_class=HTMLResponse)
    async def conversacion(request: Request, conversacion: str):
        turnos = app.state.memoria.conversacion(conversacion)
        if not turnos:
            raise HTTPException(404, "Conversación no encontrada")
        return pagina(request, "conversacion.html", "../", seccion="conversaciones",
                      conversacion=conversacion, turnos=turnos)

    @app.post("/conversaciones/borrar")
    async def borrar_todo_html():
        app.state.memoria.borrar_todo()
        return RedirectResponse("../conversaciones", status_code=303)

    @app.post("/conversaciones/{conversacion}/borrar")
    async def borrar_html(conversacion: str):
        app.state.memoria.borrar_conversacion(conversacion)
        return RedirectResponse("../../conversaciones", status_code=303)

    return app


app = crear_app()

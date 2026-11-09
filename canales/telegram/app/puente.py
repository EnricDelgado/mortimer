"""Puente Telegram ⇄ Mortimer: decide qué hacer con cada mensaje recibido.

Telegram es un canal, no un módulo: no añade conocimiento, solo otra forma de
hablar con el mismo Mortimer. Cada chat de Telegram es una conversación del
core (``tg-<chat_id>``) y queda en el registro de interacciones con
``canal = 'telegram'``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from .clientes import BotAPI, Core

log = logging.getLogger("canal.telegram")

AYUDA = """Soy Mortimer. Escríbeme como en el chat web: lo que sepa hacer con mis módulos lo hago, y si no, te lo digo.

/nueva  empieza una conversación nueva (olvida el contexto)
/modulos  qué módulos tengo instalados
/id  tu ID de Telegram
/ayuda  este mensaje"""

NO_DISPONIBLE = "Mortimer no está disponible ahora mismo. Vuelve a intentarlo en un rato."
SOLO_TEXTO = "De momento solo entiendo mensajes de texto."
ANTIGUO = ("Este mensaje llegó mientras Mortimer estaba apagado y no lo he ejecutado. "
           "Repítelo si sigue haciendo falta.")


class Conversaciones:
    """Qué conversación del core corresponde a cada chat. Se guarda en un JSON
    para que un reinicio del contenedor no corte la conversación en curso."""

    def __init__(self, ruta: Path):
        self.ruta = ruta
        self._mapa: dict[str, str] = {}
        with contextlib.suppress(FileNotFoundError, json.JSONDecodeError):
            self._mapa = json.loads(ruta.read_text())

    def de(self, chat_id: int) -> str:
        return self._mapa.get(str(chat_id), f"tg-{chat_id}")

    def nueva(self, chat_id: int) -> str:
        conv = f"tg-{chat_id}-{int(time.time())}"
        self._mapa[str(chat_id)] = conv
        try:
            self.ruta.parent.mkdir(parents=True, exist_ok=True)
            self.ruta.write_text(json.dumps(self._mapa))
        except OSError as e:  # sin disco, se pierde al reiniciar; no es grave
            log.warning("No se pudo guardar %s: %s", self.ruta, e)
        return conv


class Puente:
    def __init__(self, bot: BotAPI, core: Core, usuarios: frozenset[int],
                 conversaciones: Conversaciones, max_antiguedad: int = 600,
                 traza: bool = False, reloj=time.time):
        self.bot = bot
        self.core = core
        self.usuarios = usuarios
        self.conv = conversaciones
        self.max_antiguedad = max_antiguedad
        self.traza = traza
        self.reloj = reloj

    async def procesar(self, update: dict[str, Any]) -> None:
        msg = update.get("message")
        if not msg:
            return
        chat = msg.get("chat", {})
        chat_id = chat.get("id")
        usuario = msg.get("from", {}).get("id")
        if chat.get("type") != "private":
            log.info("Ignorado: chat %s de tipo %s", chat_id, chat.get("type"))
            return

        if usuario not in self.usuarios:
            # Sin datos de Mortimer: solo se le dice su ID, para poder darlo de alta.
            log.warning("Usuario no autorizado %s (añádelo a TELEGRAM_USUARIOS si eres tú)", usuario)
            await self.bot.enviar(chat_id, f"No estás autorizado para usar este Mortimer. Tu ID de Telegram es {usuario}.")
            return

        texto = (msg.get("text") or "").strip()
        if not texto:
            await self.bot.enviar(chat_id, SOLO_TEXTO)
            return

        if texto.startswith("/"):
            await self._comando(chat_id, usuario, texto)
            return

        if self.reloj() - msg.get("date", 0) > self.max_antiguedad:
            await self.bot.enviar(chat_id, ANTIGUO)
            return

        await self.bot.enviar(chat_id, await self._preguntar(chat_id, texto))

    async def _preguntar(self, chat_id: int, texto: str) -> str:
        # El primer "escribiendo…" se envía antes de llamar al core, para que se
        # vea al instante; después se renueva en segundo plano.
        with contextlib.suppress(Exception):
            await self.bot.escribiendo(chat_id)
        escribiendo = asyncio.create_task(self._mantener_escribiendo(chat_id))
        try:
            r = await self.core.chat(texto, self.conv.de(chat_id))
        except Exception as e:
            log.error("Fallo al llamar al core: %s: %s", type(e).__name__, e)
            return NO_DISPONIBLE
        finally:
            escribiendo.cancel()
        respuesta = r.get("texto") or "(sin respuesta)"
        if self.traza and r.get("herramientas"):
            usadas = ", ".join(f"{h['modulo']}.{h['herramienta']}" for h in r["herramientas"])
            respuesta += f"\n\n→ {usadas}"
        return respuesta

    async def _mantener_escribiendo(self, chat_id: int) -> None:
        # Telegram borra el "escribiendo…" a los 5 s; el core tarda de 6 a 25 s.
        with contextlib.suppress(Exception):
            while True:
                await asyncio.sleep(4)
                await self.bot.escribiendo(chat_id)

    async def _comando(self, chat_id: int, usuario: int, texto: str) -> None:
        orden = texto.split()[0].split("@")[0].lower()
        if orden in ("/start", "/ayuda", "/help"):
            await self.bot.enviar(chat_id, AYUDA)
        elif orden == "/nueva":
            self.conv.nueva(chat_id)
            await self.bot.enviar(chat_id, "Conversación nueva. ¿En qué te ayudo?")
        elif orden == "/id":
            await self.bot.enviar(chat_id, f"Tu ID de Telegram es {usuario}.")
        elif orden == "/modulos":
            try:
                modulos = await self.core.modulos()
            except Exception as e:
                log.error("Fallo al pedir módulos: %s", e)
                await self.bot.enviar(chat_id, NO_DISPONIBLE)
                return
            if not modulos:
                await self.bot.enviar(chat_id, "No tengo módulos instalados: solo puedo conversar.")
                return
            lineas = []
            for m in modulos:
                nombre = m.get("nombre") or m.get("url", "?")
                herramientas = m.get("herramientas") or []
                estado = "" if m.get("estado", "ok") == "ok" else " (no disponible)"
                desc = f" — {m['descripcion']}" if m.get("descripcion") else ""
                lineas.append(f"• {nombre}{estado}{desc} ({len(herramientas)} herramientas)")
            await self.bot.enviar(chat_id, "Módulos instalados:\n" + "\n".join(lineas))
        else:
            await self.bot.enviar(chat_id, "No conozco esa orden. Escribe /ayuda.")

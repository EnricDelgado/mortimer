"""Clientes HTTP: la Bot API de Telegram y la API del core de Mortimer."""

from __future__ import annotations

from typing import Any

import httpx

LIMITE_TELEGRAM = 4096  # caracteres por mensaje


class ErrorTelegram(Exception):
    pass


class BotAPI:
    """Lo mínimo de la Bot API: recibir con long polling y contestar."""

    def __init__(self, token: str, base: str = "https://api.telegram.org",
                 cliente: httpx.AsyncClient | None = None):
        self.base = f"{base.rstrip('/')}/bot{token}"
        # El long polling deja la petición abierta hasta 50 s: timeout de lectura mayor.
        self.http = cliente or httpx.AsyncClient(timeout=httpx.Timeout(10, read=70))

    async def _llamar(self, metodo: str, **datos: Any) -> Any:
        r = await self.http.post(f"{self.base}/{metodo}", json=datos)
        cuerpo = r.json()
        if not cuerpo.get("ok"):
            # El token va en la URL: no se incluye en el mensaje de error.
            raise ErrorTelegram(f"{metodo}: {cuerpo.get('description', r.status_code)}")
        return cuerpo["result"]

    async def recibir(self, offset: int | None, espera: int = 50) -> list[dict[str, Any]]:
        datos: dict[str, Any] = {"timeout": espera, "allowed_updates": ["message"]}
        if offset is not None:
            datos["offset"] = offset
        return await self._llamar("getUpdates", **datos)

    async def escribiendo(self, chat_id: int) -> None:
        await self._llamar("sendChatAction", chat_id=chat_id, action="typing")

    async def enviar(self, chat_id: int, texto: str) -> None:
        # Texto plano, sin parse_mode: así no hay que escapar Markdown ni HTML
        # en lo que genera el modelo.
        for trozo in partir(texto):
            await self._llamar("sendMessage", chat_id=chat_id, text=trozo)

    async def cerrar(self) -> None:
        await self.http.aclose()


def partir(texto: str, limite: int = LIMITE_TELEGRAM) -> list[str]:
    """Parte un texto largo en trozos que Telegram acepta, por saltos de línea si se puede."""
    trozos: list[str] = []
    while len(texto) > limite:
        corte = texto.rfind("\n", 0, limite)
        if corte <= 0:
            corte = limite
        trozos.append(texto[:corte])
        texto = texto[corte:].lstrip("\n")
    if texto or not trozos:
        trozos.append(texto)
    return trozos


class Core:
    """Cliente de la API del core. El canal no sabe nada de modelos ni de módulos."""

    def __init__(self, url: str, timeout: float = 180, cliente: httpx.AsyncClient | None = None):
        self.url = url.rstrip("/")
        self.http = cliente or httpx.AsyncClient(timeout=timeout)

    async def chat(self, texto: str, conversacion: str) -> dict[str, Any]:
        r = await self.http.post(f"{self.url}/api/chat", json={
            "texto": texto, "conversacion": conversacion, "canal": "telegram"})
        r.raise_for_status()
        return r.json()

    async def modulos(self) -> list[dict[str, Any]]:
        r = await self.http.get(f"{self.url}/api/modulos")
        r.raise_for_status()
        return r.json()

    async def cerrar(self) -> None:
        await self.http.aclose()

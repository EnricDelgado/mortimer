"""Canal de Telegram de Mortimer: bucle de long polling.

    python -m app.main

No publica ningún puerto ni necesita URL pública: es el canal quien pregunta a
Telegram si hay mensajes nuevos (getUpdates), así que funciona detrás del
router de casa sin abrir nada. Los mensajes se atienden de uno en uno, igual
que Ollama (OLLAMA_NUM_PARALLEL=1).
"""

from __future__ import annotations

import asyncio
import logging
import sys

from .clientes import BotAPI, Core
from .config import Config
from .puente import Conversaciones, Puente

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
# httpx registra cada petición con la URL completa, y la URL lleva el token.
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("canal.telegram")


async def bucle(bot: BotAPI, puente: Puente, parar: asyncio.Event | None = None,
                espera_max: float = 60) -> None:
    offset: int | None = None
    espera = 1.0
    while not (parar and parar.is_set()):
        try:
            updates = await bot.recibir(offset)
            espera = 1.0
        except Exception as e:
            # Sin red o Telegram caído: reintento con espera creciente, sin morir.
            log.warning("getUpdates falló (%s); reintento en %.0f s", type(e).__name__, espera)
            await asyncio.sleep(espera)
            espera = min(espera * 2, espera_max)
            continue
        for u in updates:
            # El offset confirma el update aunque procesarlo falle: un mensaje
            # que rompe el puente no debe repetirse en bucle.
            offset = u["update_id"] + 1
            try:
                await puente.procesar(u)
            except Exception:
                log.exception("Error procesando el update %s", u.get("update_id"))


async def principal(config: Config) -> None:
    if not config.token:
        log.error("Falta TELEGRAM_TOKEN en el entorno.")
        sys.exit(1)
    if not config.usuarios:
        log.warning("TELEGRAM_USUARIOS está vacía: el bot no atenderá a nadie. "
                    "Escríbele y mira en el log tu ID.")
    bot = BotAPI(config.token, config.api_url)
    core = Core(config.core_url, config.timeout_core)
    puente = Puente(bot, core, config.usuarios,
                    Conversaciones(config.data_dir / "conversaciones.json"),
                    config.max_antiguedad, config.traza)
    log.info("Canal de Telegram en marcha → core %s, %d usuario(s) autorizados",
             config.core_url, len(config.usuarios))
    try:
        await bucle(bot, puente)
    finally:
        await bot.cerrar()
        await core.cerrar()


if __name__ == "__main__":
    asyncio.run(principal(Config()))

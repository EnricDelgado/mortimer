"""Configuración del canal de Telegram, leída de variables de entorno."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _ids(valor: str) -> frozenset[int]:
    return frozenset(int(v) for v in valor.replace(" ", "").split(",") if v)


@dataclass(frozen=True)
class Config:
    token: str = os.environ.get("TELEGRAM_TOKEN", "")
    # IDs de usuario de Telegram autorizados. Vacía = nadie: el bot contesta a
    # cualquiera con su ID (para que sepas cuál poner aquí) y no hace nada más.
    usuarios: frozenset[int] = field(
        default_factory=lambda: _ids(os.environ.get("TELEGRAM_USUARIOS", "")))
    core_url: str = os.environ.get("CORE_URL", "http://core:8000")
    api_url: str = os.environ.get("TELEGRAM_API", "https://api.telegram.org")
    data_dir: Path = Path(os.environ.get("DATA_DIR", "./data/telegram"))
    # Mensajes más antiguos que esto (en segundos) no se ejecutan: si el servidor
    # estuvo apagado, no queremos crear de golpe eventos pedidos hace horas.
    max_antiguedad: int = int(os.environ.get("TELEGRAM_MAX_ANTIGUEDAD", "600"))
    # 1 = añade debajo de la respuesta las herramientas que se invocaron.
    traza: bool = os.environ.get("TELEGRAM_TRAZA", "0") == "1"
    # El core puede tardar (CPU sin GPU); el chat web tampoco corta antes.
    timeout_core: float = float(os.environ.get("TELEGRAM_TIMEOUT_CORE", "180"))

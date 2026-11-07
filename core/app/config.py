"""Configuración del core, leída de variables de entorno."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _lista(valor: str) -> list[str]:
    return [v.strip() for v in valor.split(",") if v.strip()]


@dataclass(frozen=True)
class Config:
    ollama_url: str = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    # DA-06: granite4.1:3b con el esquema v2 (spike del 14/10/2026).
    modelo: str = os.environ.get("MODELO", "granite4.1:3b")
    # Los modelos con "thinking" (qwen3, gemma4...) son mucho más lentos si
    # razonan antes de llamar a una herramienta. Vacío = no se envía el campo.
    think: str = os.environ.get("OLLAMA_THINK", "false")
    num_ctx: int = int(os.environ.get("NUM_CTX", "8192"))
    # Tope de tokens de salida por llamada al modelo (DA-09). 0 = sin tope.
    num_predict: int = int(os.environ.get("NUM_PREDICT", "160"))
    # URL base de cada módulo; el endpoint MCP está en <url>/mcp.
    modulos: list[str] = field(default_factory=lambda: _lista(os.environ.get("MODULOS", "")))
    zona_horaria: str = os.environ.get("ZONA_HORARIA", "Europe/Madrid")
    data_dir: Path = Path(os.environ.get("DATA_DIR", "./data/core"))
    max_rondas_herramientas: int = int(os.environ.get("MAX_RONDAS", "4"))
    historial_mensajes: int = int(os.environ.get("HISTORIAL", "10"))

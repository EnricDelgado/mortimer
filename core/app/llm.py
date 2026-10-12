"""Cliente mínimo de la API de chat de Ollama, con soporte de tool calling.

Se usa la API nativa (/api/chat) y no la compatible con OpenAI porque
devuelve las métricas de tiempo de Ollama, que van al registro de
interacciones y a la evaluación de rendimiento.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx


@dataclass
class LlamadaHerramienta:
    nombre: str
    argumentos: dict[str, Any]


@dataclass
class RespuestaLLM:
    contenido: str
    llamadas: list[LlamadaHerramienta] = field(default_factory=list)
    mensaje_crudo: dict[str, Any] = field(default_factory=dict)
    metricas: dict[str, Any] = field(default_factory=dict)


class LLM(Protocol):
    modelo: str

    async def chat(self, mensajes: list[dict], herramientas: list[dict]) -> RespuestaLLM: ...


def _argumentos(valor: Any) -> dict[str, Any]:
    # Algunos modelos devuelven los argumentos como cadena JSON en vez de objeto.
    if isinstance(valor, dict):
        return valor
    if isinstance(valor, str) and valor.strip():
        try:
            datos = json.loads(valor)
            return datos if isinstance(datos, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


class OllamaLLM:
    def __init__(self, url: str, modelo: str, think: str = "false", num_ctx: int = 8192,
                 timeout: float = 300.0):
        self.url = url.rstrip("/")
        self.modelo = modelo
        self.think = think
        self.num_ctx = num_ctx
        self._http = httpx.AsyncClient(timeout=timeout)

    async def chat(self, mensajes: list[dict], herramientas: list[dict]) -> RespuestaLLM:
        cuerpo: dict[str, Any] = {
            "model": self.modelo,
            "messages": mensajes,
            "stream": False,
            # Temperatura 0: las decisiones de enrutado deben ser lo más
            # reproducibles posible (el modelo sigue sin ser determinista).
            # num_ctx explícito: si el contexto por defecto de Ollama se queda
            # corto, recorta el prompt sin avisar y el modelo pierde las
            # herramientas. Fijarlo también evita recargar el modelo.
            "options": {"temperature": 0, "num_ctx": self.num_ctx},
        }
        if herramientas:
            cuerpo["tools"] = herramientas
        if self.think in ("true", "false"):
            cuerpo["think"] = self.think == "true"

        r = await self._http.post(f"{self.url}/api/chat", json=cuerpo)
        if r.status_code == 400 and "think" in cuerpo and "think" in r.text.lower():
            # El modelo no admite el parámetro think: se repite sin él.
            cuerpo.pop("think")
            r = await self._http.post(f"{self.url}/api/chat", json=cuerpo)
        r.raise_for_status()
        datos = r.json()
        msg = datos.get("message", {})

        llamadas = [
            LlamadaHerramienta(
                nombre=tc.get("function", {}).get("name", ""),
                argumentos=_argumentos(tc.get("function", {}).get("arguments")),
            )
            for tc in msg.get("tool_calls") or []
        ]
        ns = 1_000_000  # Ollama da las duraciones en nanosegundos
        metricas = {
            "total_ms": datos.get("total_duration", 0) / ns,
            "carga_ms": datos.get("load_duration", 0) / ns,
            "prompt_tokens": datos.get("prompt_eval_count"),
            "prompt_ms": datos.get("prompt_eval_duration", 0) / ns,
            "salida_tokens": datos.get("eval_count"),
            "salida_ms": datos.get("eval_duration", 0) / ns,
        }
        return RespuestaLLM(
            contenido=msg.get("content", "") or "",
            llamadas=llamadas,
            mensaje_crudo=msg,
            metricas=metricas,
        )

    async def cerrar(self) -> None:
        await self._http.aclose()

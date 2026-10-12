"""Registro de módulos: descubre las herramientas de cada módulo vía MCP.

El core es el único que conoce el mapa herramienta → módulo. Los módulos
no se conocen entre sí. Un módulo caído no impide arrancar: el core sigue
conversando con los que respondan (RF-01).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from mcp import Client

log = logging.getLogger("mortimer.registro")

# Un destino es la URL base de un módulo o, en los tests, un servidor MCP en memoria.
Destino = Any


@dataclass
class Herramienta:
    nombre: str
    modulo: str
    descripcion: str
    esquema: dict[str, Any]
    destino: Destino

    def para_llm(self) -> dict[str, Any]:
        """Formato de herramienta que espera Ollama (estilo OpenAI)."""
        esquema = dict(self.esquema or {"type": "object", "properties": {}})
        esquema.pop("title", None)
        return {
            "type": "function",
            "function": {
                "name": self.nombre,
                "description": self.descripcion,
                "parameters": esquema,
            },
        }


@dataclass
class InfoModulo:
    nombre: str
    version: str
    descripcion: str
    url: str | None
    herramientas: list[str]
    estado: str  # "ok" | "error"
    error: str | None = None


@dataclass
class ResultadoHerramienta:
    ok: bool
    texto: str
    datos: Any = None


def _endpoint(destino: Destino) -> Destino:
    if isinstance(destino, str):
        return destino.rstrip("/") + "/mcp"
    return destino


class Registro:
    def __init__(self, destinos: list[Destino]):
        self.destinos = destinos
        self.herramientas: dict[str, Herramienta] = {}
        self.modulos: list[InfoModulo] = []

    async def actualizar(self) -> None:
        herramientas: dict[str, Herramienta] = {}
        modulos: list[InfoModulo] = []
        for destino in self.destinos:
            url = destino if isinstance(destino, str) else None
            try:
                async with Client(_endpoint(destino), read_timeout_seconds=10) as cliente:
                    info = cliente.server_info
                    nombre = info.name
                    listado = await cliente.list_tools()
                    nombres = []
                    for t in listado.tools:
                        if t.name in herramientas:
                            # Sin prefijos: nombres naturales ayudan a los modelos
                            # pequeños. A cambio, una colisión se rechaza aquí.
                            log.warning(
                                "Herramienta %s de %s ignorada: ya la ofrece %s",
                                t.name, nombre, herramientas[t.name].modulo,
                            )
                            continue
                        herramientas[t.name] = Herramienta(
                            nombre=t.name,
                            modulo=nombre,
                            descripcion=t.description or "",
                            esquema=t.input_schema or {},
                            destino=destino,
                        )
                        nombres.append(t.name)
                    modulos.append(InfoModulo(
                        nombre=nombre,
                        version=info.version or "",
                        descripcion=cliente.instructions or "",
                        url=url,
                        herramientas=nombres,
                        estado="ok",
                    ))
            except Exception as e:  # módulo caído o mal configurado
                log.error("No se pudo registrar el módulo %s: %s", destino, e)
                modulos.append(InfoModulo(
                    nombre=str(destino), version="", descripcion="", url=url,
                    herramientas=[], estado="error", error=str(e),
                ))
        self.herramientas = herramientas
        self.modulos = modulos

    def para_llm(self) -> list[dict[str, Any]]:
        return [h.para_llm() for h in self.herramientas.values()]

    async def invocar(self, nombre: str, argumentos: dict[str, Any]) -> tuple[str | None, ResultadoHerramienta]:
        """Llama a la herramienta en su módulo. Devuelve (módulo, resultado)."""
        h = self.herramientas.get(nombre)
        if h is None:
            return None, ResultadoHerramienta(False, f"La herramienta '{nombre}' no existe.")
        try:
            async with Client(_endpoint(h.destino), read_timeout_seconds=30) as cliente:
                r = await cliente.call_tool(nombre, argumentos)
        except Exception as e:
            return h.modulo, ResultadoHerramienta(False, f"Error al llamar a {h.modulo}: {e}")
        texto = "\n".join(c.text for c in r.content if getattr(c, "type", "") == "text")
        return h.modulo, ResultadoHerramienta(not r.is_error, texto, r.structured_content)

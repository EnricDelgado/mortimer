"""Módulo de prueba de Mortimer.

Es el módulo mínimo que cumple el contrato: un servidor MCP con una
herramienta, una página propia (/ui) y un /health. Sirve para comprobar el
bucle completo del Incremento 0 y como plantilla para docs/crear-un-modulo.md.
"""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

NOMBRE = "prueba"
VERSION = "0.1.0"

mcp = MCPServer(
    NOMBRE,
    version=VERSION,
    instructions="Módulo de prueba: informa de la fecha y la hora actuales.",
)


@mcp.tool()
def dime_la_hora(zona: str = "Europe/Madrid") -> str:
    """Devuelve la fecha y la hora actuales en una zona horaria IANA.

    Úsala cuando el usuario pregunte qué hora es o qué día es hoy.
    """
    try:
        tz = ZoneInfo(zona)
    except (ZoneInfoNotFoundError, ValueError):
        return f"Zona horaria desconocida: {zona}"
    ahora = datetime.now(tz)
    return ahora.strftime(f"%Y-%m-%d %H:%M ({zona})")


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"modulo": NOMBRE, "version": VERSION, "estado": "ok"})


@mcp.custom_route("/ui", methods=["GET"])
async def ui(_: Request) -> HTMLResponse:
    # Todo módulo de Mortimer tiene frontend propio. El de este es mínimo.
    return HTMLResponse(
        f"<!doctype html><meta charset=utf-8><title>Mortimer · {NOMBRE}</title>"
        f"<h1>Módulo {NOMBRE} v{VERSION}</h1>"
        "<p>Herramientas: <code>dime_la_hora</code>. No guarda datos.</p>"
    )


# Protección contra DNS rebinding: solo se aceptan peticiones dirigidas a
# los nombres desde los que el core llama al módulo (localhost en desarrollo,
# el nombre del servicio dentro de la red de Docker).
_hosts = os.environ.get("ALLOWED_HOSTS", f"localhost:*,127.0.0.1:*,{NOMBRE}:*")
app = mcp.streamable_http_app(
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[h.strip() for h in _hosts.split(",") if h.strip()],
    ),
)

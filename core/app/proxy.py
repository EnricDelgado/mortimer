"""Proxy de frontends de módulos (DEV-08, DA-10).

GET|POST|PATCH|PUT|DELETE /m/<modulo>/<ruta> se reenvía a <url del módulo>/<ruta>.
Así los frontends, la API REST y el .ics de cada módulo se ven desde la red de
casa por el puerto del core, sin publicar puertos de los módulos (DA-05).

- Conserva método, cuerpo, query string y Content-Type. El Host lo pone httpx
  a partir de la URL del módulo, que es el que el módulo tiene permitido.
- No reenvía /mcp: el protocolo MCP es solo entre el core y los módulos.
- 404 si el módulo no existe o no tiene URL; 502 si no responde.
"""

from __future__ import annotations

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response

from .registro import Registro

METODOS = ["GET", "POST", "PATCH", "PUT", "DELETE"]
# Cabeceras de la respuesta del módulo que se devuelven al navegador.
_CABECERAS_RESPUESTA = {"content-type", "content-disposition", "cache-control", "etag",
                        "last-modified", "location", "hx-redirect", "hx-trigger", "hx-push-url"}
# Cabeceras de la petición que se reenvían al módulo.
_CABECERAS_PETICION = {"content-type", "accept", "accept-language", "if-none-match",
                       "if-modified-since", "hx-request", "hx-target", "hx-current-url", "hx-trigger"}


class Proxy:
    def __init__(self, registro: Registro, transporte: httpx.AsyncBaseTransport | None = None,
                 timeout: float = 30.0):
        self.registro = registro
        self._http = httpx.AsyncClient(transport=transporte, timeout=timeout, follow_redirects=False)

    def _url(self, modulo: str) -> str | None:
        for m in self.registro.modulos:
            if m.nombre == modulo and m.url:
                return m.url.rstrip("/")
        return None

    async def reenviar(self, request: Request, modulo: str, ruta: str) -> Response:
        if ruta.split("/", 1)[0] == "mcp":
            return JSONResponse({"error": "No encontrado"}, status_code=404)
        base = self._url(modulo)
        if base is None:
            # Puede que el módulo arrancase después que el core (QA C-01).
            await self.registro.reintentar_caidos()
            base = self._url(modulo)
        if base is None:
            return JSONResponse({"error": f"Módulo '{modulo}' no encontrado"}, status_code=404)
        cabeceras = {k: v for k, v in request.headers.items() if k.lower() in _CABECERAS_PETICION}
        try:
            r = await self._http.request(
                request.method, f"{base}/{ruta}", params=list(request.query_params.multi_items()),
                content=await request.body(), headers=cabeceras,
            )
        except httpx.HTTPError as e:
            return JSONResponse({"error": f"El módulo '{modulo}' no responde: {type(e).__name__}"},
                                status_code=502)
        salida = {k: v for k, v in r.headers.items() if k.lower() in _CABECERAS_RESPUESTA}
        return Response(r.content, status_code=r.status_code, headers=salida)

    async def cerrar(self) -> None:
        await self._http.aclose()

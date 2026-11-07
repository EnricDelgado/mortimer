"""DEV-08 (DA-10): proxy /m/<modulo>/<ruta> hacia el frontend y la API de cada módulo."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI, Request

from app.proxy import METODOS, Proxy
from app.registro import InfoModulo, Registro


def registro_falso() -> Registro:
    r = Registro([])
    r.modulos = [
        InfoModulo("calendario", "0.1.0", "", "http://calendario-stg:8001", [], "ok"),
        InfoModulo("caido", "0.1.0", "", "http://caido:8001", [], "ok"),
        InfoModulo("enmemoria", "0.1.0", "", None, [], "ok"),
    ]
    return r


def modulo(request: httpx.Request) -> httpx.Response:
    if request.url.host == "caido":
        raise httpx.ConnectError("sin conexión", request=request)
    if request.url.path == "/ui":
        return httpx.Response(200, html="<a href='ui/mes'>mes</a>")
    if request.url.path == "/calendario.ics":
        return httpx.Response(200, text="BEGIN:VCALENDAR", headers={"Content-Type": "text/calendar; charset=utf-8"})
    if request.url.path == "/api/eventos" and request.method == "POST":
        return httpx.Response(201, json={"recibido": request.content.decode(),
                                         "tipo": request.headers.get("content-type"),
                                         "host": request.headers.get("host"),
                                         "q": request.url.query.decode()})
    return httpx.Response(404, json={"error": "no"})


@pytest.fixture
def cliente():
    proxy = Proxy(registro_falso(), transporte=httpx.MockTransport(modulo))
    app = FastAPI()

    @app.api_route("/m/{nombre}/{ruta:path}", methods=METODOS)
    async def ruta(request: Request, nombre: str, ruta: str):
        return await proxy.reenviar(request, nombre, ruta)

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://core")


async def test_frontend(cliente):
    r = await cliente.get("/m/calendario/ui")
    assert r.status_code == 200 and "ui/mes" in r.text


async def test_ics_conserva_el_content_type(cliente):
    r = await cliente.get("/m/calendario/calendario.ics")
    assert r.text == "BEGIN:VCALENDAR" and r.headers["content-type"].startswith("text/calendar")


async def test_post_con_cuerpo_query_y_host_del_modulo(cliente):
    r = await cliente.post("/m/calendario/api/eventos?x=1", content=b'{"titulo":"a"}',
                           headers={"Content-Type": "application/json"})
    d = r.json()
    assert r.status_code == 201
    assert d == {"recibido": '{"titulo":"a"}', "tipo": "application/json",
                 "host": "calendario-stg:8001", "q": "x=1"}


async def test_mcp_no_se_reenvia(cliente):
    assert (await cliente.post("/m/calendario/mcp")).status_code == 404
    assert (await cliente.get("/m/calendario/mcp/algo")).status_code == 404


async def test_modulo_inexistente_o_sin_url(cliente):
    assert (await cliente.get("/m/noexiste/ui")).status_code == 404
    assert (await cliente.get("/m/enmemoria/ui")).status_code == 404


async def test_modulo_caido_502(cliente):
    assert (await cliente.get("/m/caido/ui")).status_code == 502


async def test_reintenta_modulos_caidos(monkeypatch):
    r = Registro(["http://127.0.0.1:1"])
    await r.actualizar()
    assert r.modulos[0].estado == "error"
    llamadas = []

    async def actualizar():
        llamadas.append(1)
    monkeypatch.setattr(r, "actualizar", actualizar)
    await r.reintentar_caidos(cada=15)   # acaba de intentarlo: no repite
    assert llamadas == []
    await r.reintentar_caidos(cada=0)
    assert llamadas == [1]

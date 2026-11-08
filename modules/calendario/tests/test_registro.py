"""Pestaña «Registro» del frontend y registro de actividad del módulo.

Cada llamada a una herramienta (canal 'chat') y cada cambio hecho a mano
('manual') o por la API ('api') queda anotado en la tabla `actividad`.
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from mcp import Client
from starlette.testclient import TestClient

from app.db import BaseDatos
from app.dominio import Calendario
from app.main import crear_app, crear_servidor

MADRID = ZoneInfo("Europe/Madrid")
AHORA = datetime(2026, 10, 29, 18, 0, tzinfo=MADRID)
HTMX = {"HX-Request": "true"}
URL_ABSOLUTA = re.compile(
    r'\b(?:href|src|action|hx-get|hx-post|data-core)\s*=\s*["\']\s*(?:/|[a-z]+:)', re.I)


@pytest.fixture
def servicio(tmp_path):
    return Calendario(BaseDatos(tmp_path / "calendario.db"), MADRID, reloj=lambda: AHORA)


@pytest.fixture
def servidor(servicio):
    return crear_servidor(servicio)


@pytest.fixture
def cliente(servidor):
    return TestClient(crear_app(servidor, hosts="testserver,localhost:*,calendario:*"))


def entradas(servicio, **filtro):
    return servicio.db.actividad(**filtro)


# ── Qué se anota ───────────────────────────────────────────────────────────

async def test_las_herramientas_se_anotan_con_argumentos_y_resultado(servidor, servicio):
    async with Client(servidor) as c:
        await c.call_tool("crear_evento", {"titulo": "Dentista", "dia": "viernes", "hora": "16:00"})
        await c.call_tool("crear_evento", {"titulo": "Dentista", "dia": "sabado", "hora": "10:00"})
        await c.call_tool("cancelar_evento", {"titulo": "dentista"})  # ambiguo: pregunta
        await c.call_tool("listar_eventos", {"periodo": "esta semana"})
    lista = entradas(servicio)
    assert [e["operacion"] for e in lista] == ["listar_eventos", "cancelar_evento", "crear_evento", "crear_evento"]
    assert {e["canal"] for e in lista} == {"chat"}
    listar, cancelar, crear2, crear1 = lista
    assert crear1["argumentos"] == {"titulo": "Dentista", "dia": "viernes", "hora": "16:00",
                                    "hora_fin": "", "ubicacion": ""}
    assert crear1["estado"] == "ok" and crear1["evento_id"]
    assert crear1["resultado"].startswith("Hecho")
    assert crear1["ms"] is not None
    assert cancelar["estado"] == "pregunta" and cancelar["evento_id"] is None
    assert listar["estado"] == "ok" and "Dentista" in listar["resultado"]


async def test_cancelar_y_editar_por_chat_apuntan_al_evento(servidor, servicio):
    async with Client(servidor) as c:
        await c.call_tool("crear_evento", {"titulo": "Yoga", "dia": "viernes", "hora": "09:00"})
        await c.call_tool("editar_evento", {"titulo": "yoga", "nueva_hora": "10:00"})
        await c.call_tool("cancelar_evento", {"titulo": "yoga"})
    cancelar, editar, crear = entradas(servicio)
    assert crear["evento_id"] == editar["evento_id"] == cancelar["evento_id"]
    assert editar["estado"] == cancelar["estado"] == "ok"


def test_cambios_a_mano_y_por_api(cliente, servicio):
    r = cliente.post("/ui/eventos", headers=HTMX, data={
        "titulo": "Cena", "fecha": "2026-10-30", "hora": "21:00", "raiz": "", "volver": "semana:2026-10-26"})
    assert r.status_code == 200
    cliente.post("/ui/eventos", headers=HTMX, data={"titulo": "", "fecha": "2026-10-30", "raiz": ""})
    ev = cliente.post("/api/eventos", json={"titulo": "Fisio", "inicio": "2026-10-31T08:30"}).json()["evento"]
    cliente.patch(f"/api/eventos/{ev['id']}", json={"titulo": "Fisioterapia"})
    cliente.post(f"/ui/eventos/{ev['id']}/cancelar", headers=HTMX, data={"raiz": ""})
    cliente.post(f"/ui/eventos/{ev['id']}/restaurar", headers=HTMX, data={"raiz": ""})

    lista = entradas(servicio)
    assert [(e["canal"], e["operacion"], e["estado"]) for e in lista] == [
        ("manual", "restaurar", "ok"), ("manual", "cancelar", "ok"),
        ("api", "editar", "ok"), ("api", "alta", "ok"),
        ("manual", "alta", "error"), ("manual", "alta", "ok"),
    ]
    alta = lista[-1]
    # No se guardan los campos internos del formulario ni los vacíos.
    assert alta["argumentos"] == {"titulo": "Cena", "fecha": "2026-10-30", "hora": "21:00"}
    assert alta["resultado"].startswith("Añadido: 'Cena'")
    assert entradas(servicio, canal="api")[0]["argumentos"] == {"titulo": "Fisioterapia"}


def test_las_consultas_no_se_anotan(cliente, servicio):
    cliente.get("/ui")
    cliente.get("/ui/mes")
    cliente.get("/api/eventos")
    cliente.get("/calendario.ics")
    assert entradas(servicio) == []


# ── Pestaña ───────────────────────────────────────────────────────────────

async def test_pagina_de_registro(servidor, servicio, cliente):
    async with Client(servidor) as c:
        await c.call_tool("crear_evento", {"titulo": "Taller", "dia": "viernes", "hora": "10:00"})
    cliente.post("/ui/eventos", data={"titulo": "Cena <b>", "fecha": "2026-10-30", "hora": "21:00"})
    html = cliente.get("/ui/registro").text
    assert "<title>Registro · Mortimer</title>" in html
    assert 'aria-current="page">Registro</a>' in html
    # Llamada a la herramienta tal cual, sin argumentos vacíos.
    assert '→ crear_evento(titulo=&#34;Taller&#34;, dia=&#34;viernes&#34;, hora=&#34;10:00&#34;)' in html
    assert "Mortimer</span>" in html and "A mano</span>" in html
    # Enlace al evento en su semana, relativo a la raíz del módulo.
    assert 'href="../ui?semana=2026-10-30#d-2026-10-30">Ver «Taller» en el calendario' in html
    # Se escapa lo que escribe el usuario.
    assert "Cena <b>" not in html and "Cena &lt;b&gt;" in html
    # Las peticiones se leen del core, relativo a la página.
    assert 'data-core="../../../"' in html and 'data-modulo="calendario"' in html
    # La suscripción al .ics solo está en la pestaña del calendario.
    assert 'id="t-suscribir"' not in html
    assert not URL_ABSOLUTA.findall(html), URL_ABSOLUTA.findall(html)


def test_filtro_por_canal_y_parcial_htmx(cliente):
    cliente.post("/api/eventos", json={"titulo": "Por API", "inicio": "2026-10-30T10:00"})
    cliente.post("/ui/eventos", data={"titulo": "A mano", "fecha": "2026-10-30"})
    html = cliente.get("/ui/registro", params={"canal": "manual"}).text
    assert "Alta" in html and "título: A mano" in html and "Por API" not in html
    assert 'class="filtro actual" aria-current="page"' in html
    parcial = cliente.get("/ui/registro", params={"canal": "api"}, headers=HTMX).text
    assert "<html" not in parcial and "Por API" in parcial and "título: A mano" not in parcial
    # Un canal desconocido enseña todo.
    todo = cliente.get("/ui/registro", params={"canal": "otro"}).text
    assert "Por API" in todo and "A mano" in todo


def test_paginacion(cliente, servicio, monkeypatch):
    import app.web as web
    monkeypatch.setattr(web, "LIMITE_REGISTRO", 3)
    for i in range(5):
        servicio.anotar("api", "alta", {"titulo": f"E{i}"}, f"Añadido E{i}")
    html = cliente.get("/ui/registro").text
    assert "E4" in html and "E2" in html and "E1" not in html
    siguiente = re.search(r'href="\.\./(ui/registro\?antes=\d+)">Anteriores', html).group(1)
    html2 = cliente.get("/" + siguiente).text
    assert "E1" in html2 and "E0" in html2 and "E2" not in html2
    assert "Más recientes" in html2 and "Anteriores" not in html2


def test_vaciar_registro_no_toca_los_eventos(cliente, servicio):
    cliente.post("/ui/eventos", data={"titulo": "Cena", "fecha": "2026-10-30"})
    r = cliente.post("/ui/registro/vaciar", headers=HTMX, data={"raiz": "../../"})
    assert r.status_code == 200 and "Registro vaciado (1 entrada)" in r.text
    assert entradas(servicio) == []
    assert [e["titulo"] for e in servicio.listar_rango()] == ["Cena"]
    assert "Todavía no hay actividad" in r.text


def test_pestanas_y_chat_flotante_en_el_calendario(cliente):
    html = cliente.get("/ui").text
    assert 'href="ui/registro">Registro</a>' in html
    assert 'aria-current="page">Calendario</a>' in html
    # El chat es el de Mortimer (core), no uno propio del módulo.
    assert '<script src="../../static/chat-flotante.js" data-core="../../" defer></script>' in html
    mes = cliente.get("/ui/mes").text
    assert '<script src="../../../static/chat-flotante.js" data-core="../../../" defer></script>' in mes
    assert "mortimer:respuesta" in html

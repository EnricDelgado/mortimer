"""Pruebas de las rutas HTTP del Calendario (DEV-05) con el TestClient de Starlette."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import icalendar
import pytest
from starlette.testclient import TestClient

from app.db import BaseDatos
from app.dominio import Calendario
from app.main import crear_app, crear_servidor

MADRID = ZoneInfo("Europe/Madrid")
AHORA = datetime(2026, 11, 3, 9, 10, tzinfo=MADRID)


@pytest.fixture
def servicio(tmp_path):
    return Calendario(BaseDatos(tmp_path / "calendario.db"), MADRID, reloj=lambda: AHORA)


@pytest.fixture
def cliente(servicio):
    app = crear_app(crear_servidor(servicio), hosts="testserver,localhost:*,127.0.0.1:*,calendario:*")
    return TestClient(app)


def test_health(cliente):
    assert cliente.get("/health").json() == {"modulo": "calendario", "version": "0.1.0", "estado": "ok"}


def test_alta_listado_edicion_cancelacion_y_restauracion(cliente):
    r = cliente.post("/api/eventos", json={"titulo": "Fisio", "inicio": "2026-11-05T08:30",
                                           "ubicacion": "Clínica Sol"})
    assert r.status_code == 201
    fisio = r.json()["evento"]
    assert fisio["origen"] == "usuario" and fisio["fin"] == "2026-11-05T09:30:00+01:00"
    assert r.json()["solapamientos"] == []

    r = cliente.post("/api/eventos", json={"titulo": "Café", "inicio": "2026-11-05T09:00:00+01:00",
                                           "fin": "2026-11-05T09:45:00+01:00"})
    cafe = r.json()["evento"]
    assert [s["id"] for s in r.json()["solapamientos"]] == [fisio["id"]]

    lista = cliente.get("/api/eventos", params={"desde": "2026-11-05", "hasta": "2026-11-05"}).json()
    assert [e["titulo"] for e in lista] == ["Fisio", "Café"]
    assert cliente.get("/api/eventos", params={"desde": "2026-11-06", "hasta": "2026-11-30"}).json() == []
    assert cliente.get(f"/api/eventos/{fisio['id']}").json()["titulo"] == "Fisio"

    r = cliente.patch(f"/api/eventos/{cafe['id']}", json={"inicio": "2026-11-05T10:00"})
    assert r.status_code == 200
    assert r.json()["evento"]["fin"] == "2026-11-05T10:45:00+01:00" and r.json()["solapamientos"] == []

    r = cliente.post(f"/api/eventos/{fisio['id']}/cancelar")
    assert r.status_code == 200 and r.json()["evento"]["cancelado_en"]
    assert [e["titulo"] for e in cliente.get("/api/eventos").json()] == ["Café"]
    todos = cliente.get("/api/eventos", params={"incluir_cancelados": "1"}).json()
    assert len(todos) == 2

    r = cliente.post(f"/api/eventos/{fisio['id']}/restaurar")
    assert r.status_code == 200 and r.json()["evento"]["cancelado_en"] is None
    assert len(cliente.get("/api/eventos").json()) == 2


def test_errores_de_la_api(cliente):
    assert cliente.post("/api/eventos", json={"inicio": "2026-11-05T08:30"}).status_code == 400
    assert cliente.post("/api/eventos", json={"titulo": "x", "inicio": "mañana"}).status_code == 400
    # sin Content-Type JSON → 415 (QA A-07); JSON mal formado → 400
    assert cliente.post("/api/eventos", content=b"no es json").status_code == 415
    assert cliente.post("/api/eventos", content=b"no es json",
                        headers={"content-type": "application/json"}).status_code == 400
    assert cliente.patch("/api/eventos/no-existe", json={"titulo": "x"}).status_code == 404
    assert cliente.post("/api/eventos/no-existe/cancelar").status_code == 404
    assert cliente.post("/api/eventos/no-existe/restaurar").status_code == 404
    assert cliente.get("/api/eventos/no-existe").status_code == 404
    assert cliente.get("/api/eventos", params={"desde": "ayer"}).status_code == 400


def test_ics(cliente):
    cliente.post("/api/eventos", json={"titulo": "Cena, con amigos; en casa", "inicio": "2026-11-06T21:00"})
    r = cliente.post("/api/eventos", json={"titulo": "Borrado", "inicio": "2026-11-07T10:00"})
    cliente.post(f"/api/eventos/{r.json()['evento']['id']}/cancelar")
    r = cliente.get("/calendario.ics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/calendar")
    assert r.text.startswith("BEGIN:VCALENDAR")
    eventos = icalendar.Calendar.from_ical(r.text).walk("VEVENT")
    assert [str(e["SUMMARY"]) for e in eventos] == ["Cena, con amigos; en casa"]


def test_reset_protegido_por_variable(cliente, monkeypatch):
    agenda = {"eventos": [{"titulo": "Fisio", "inicio": "2026-11-05T08:30"},
                          {"titulo": "Puente", "inicio": "2026-11-09", "todo_el_dia": True}]}
    monkeypatch.delenv("CALENDARIO_PERMITIR_RESET", raising=False)
    assert cliente.post("/api/reset", json=agenda).status_code == 403
    monkeypatch.setenv("CALENDARIO_PERMITIR_RESET", "0")
    assert cliente.post("/api/reset", json=agenda).status_code == 403
    assert cliente.get("/api/eventos").json() == []

    monkeypatch.setenv("CALENDARIO_PERMITIR_RESET", "1")
    cliente.post("/api/eventos", json={"titulo": "Se borrará", "inicio": "2026-11-04T10:00"})
    r = cliente.post("/api/reset", json=agenda)
    assert r.status_code == 200 and r.json() == {"eventos": 2}
    assert [e["titulo"] for e in cliente.get("/api/eventos").json()] == ["Fisio", "Puente"]
    assert cliente.post("/api/reset", json={"eventos": [{"titulo": "sin inicio"}]}).status_code == 400


def test_rechaza_host_no_permitido(cliente):
    for ruta in ("/health", "/api/eventos", "/calendario.ics", "/ui"):
        r = cliente.get(ruta, headers={"Host": "malicioso.com"})
        assert r.status_code == 421, ruta
    r = cliente.post("/mcp", headers={"Host": "malicioso.com"}, json={})
    assert r.status_code == 421
    assert cliente.get("/health", headers={"Host": "calendario:8001"}).status_code == 200


def test_ui_esqueleto_con_enlaces_relativos(cliente):
    cliente.post("/api/eventos", json={"titulo": "Fisio", "inicio": "2026-11-05T08:30"})
    r = cliente.get("/ui")
    assert r.status_code == 200 and "Fisio" in r.text
    assert 'href="/' not in r.text and "href='/" not in r.text and 'src="/' not in r.text

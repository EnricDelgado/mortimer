"""Regresiones de la revisión QA del 30/10/2026 (A-01 a A-07, /ui).

Cada prueba reproduce un hallazgo de files/qa/revision-codigo-2026-10-30.md
con frases y eventos propios (no del banco de evaluación).
Reloj fijo: martes 3/11/2026 09:10 en Madrid.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from mcp import Client
from starlette.testclient import TestClient

from app import fechas
from app.db import BaseDatos
from app.dominio import Calendario, ErrorValidacion, a_bool, minutos_desde_texto
from app.main import crear_app, crear_servidor

MADRID = ZoneInfo("Europe/Madrid")
AHORA = datetime(2026, 11, 3, 9, 10, tzinfo=MADRID)


@pytest.fixture
def cal(tmp_path):
    return Calendario(BaseDatos(tmp_path / "calendario.db"), MADRID, reloj=lambda: AHORA)


@pytest.fixture
def cliente(cal):
    return TestClient(crear_app(crear_servidor(cal), hosts="testserver,localhost:*,127.0.0.1:*"))


def alta(cal, titulo, inicio, **kw):
    return cal.alta_directa(titulo, inicio, **kw)["evento"]


def activos(cal):
    return {e["titulo"] for e in cal.db.listar()}


# ── A-01: cancelar el evento equivocado ───────────────────────────────────

def test_a01_coincidencia_exacta_gana_frente_a_inclusion_inversa(cal):
    alta(cal, "Peluquería", "2026-11-08T10:00")
    tinte = alta(cal, "Peluquería (tinte)", "2026-11-15T10:00")
    r = cal.cancelar("peluquería del tinte")
    assert r["datos"]["cancelado"] is not None and r["datos"]["cancelado"]["id"] == tinte["id"]
    assert activos(cal) == {"Peluquería"}


def test_a01_frase_entera_como_titulo_no_cancela_el_corto(cal):
    alta(cal, "Peluquería", "2026-11-08T10:00")
    alta(cal, "Peluquería (tinte)", "2026-11-15T10:00")
    r = cal.cancelar("cancela la peluquería del tinte")
    # o cancela 'Peluquería (tinte)' o pregunta; nunca cancela 'Peluquería'
    cancelado = r["datos"]["cancelado"]
    assert cancelado is None or cancelado["titulo"] == "Peluquería (tinte)"
    assert "Peluquería" in activos(cal)
    if cancelado is None:
        assert r["texto"].endswith("?") and "Peluquería (tinte)" in r["texto"]


@pytest.mark.parametrize("existente,dicho", [
    ("Cena", "cena de empresa"),
    ("Médico", "medico de familia"),
    ("Peluquería", "peluquería del tinte"),
])
def test_a01_inclusion_inversa_pregunta_y_no_cancela(cal, existente, dicho):
    ev = alta(cal, existente, "2026-11-08T10:00")
    r = cal.cancelar(dicho)
    assert r["final"] is True and r["datos"]["cancelado"] is None
    assert [c["id"] for c in r["datos"]["candidatos"]] == [ev["id"]]
    assert f"'{existente}'" in r["texto"] and r["texto"].endswith("?")
    assert activos(cal) == {existente}


def test_a01_exacta_frente_a_otra_mas_corta(cal):
    alta(cal, "Cena", "2026-11-06T21:00")
    empresa = alta(cal, "Cena de empresa", "2026-11-20T21:00")
    r = cal.cancelar("cena de empresa")
    assert r["datos"]["cancelado"]["id"] == empresa["id"]
    assert activos(cal) == {"Cena"}


def test_a01_errata_pregunta_y_no_cancela(cal):
    ev = alta(cal, "Entrevista", "2026-11-16T12:00")
    r = cal.cancelar("la entrevsta")
    assert r["datos"]["cancelado"] is None and r["datos"]["candidatos"][0]["id"] == ev["id"]
    assert "Lo más parecido es 'Entrevista'" in r["texto"] and r["texto"].endswith("?")
    assert activos(cal) == {"Entrevista"}
    # la respuesta trae el id: con él sí se cancela
    assert cal.cancelar(id=ev["id"])["datos"]["cancelado"]["id"] == ev["id"]


def test_a01_inclusion_inversa_con_dia(cal):
    alta(cal, "Cena", "2026-11-06T21:00")
    r = cal.cancelar("cena de empresa", "viernes")
    assert r["datos"]["cancelado"] is None and activos(cal) == {"Cena"}


# ── A-02: fecha ISO imposible ─────────────────────────────────────────────

@pytest.mark.parametrize("expr", ["2026-13-45", "2026-02-30", "2026-00-10"])
def test_a02_fecha_iso_imposible_es_none(expr):
    assert fechas.resolver_dia(expr, date(2026, 11, 3)) is None


@pytest.mark.parametrize("herramienta,argumentos", [
    ("crear_evento", {"titulo": "Prueba", "dia": "2026-13-45"}),
    ("cancelar_evento", {"titulo": "prueba", "dia": "2026-02-30"}),
    ("buscar_huecos", {"dia": "2026-02-30", "duracion_min": 30}),
    ("listar_eventos", {"periodo": "2026-13-45"}),
])
async def test_a02_herramientas_responden_final(cal, herramienta, argumentos):
    async with Client(crear_servidor(cal)) as c:
        r = await c.call_tool(herramienta, argumentos)
    assert r.is_error is False
    assert r.structured_content["final"] is True
    assert r.structured_content["texto"].startswith("No entiendo")


# ── A-03: reset con ids repetidos ─────────────────────────────────────────

def test_a03_reset_con_ids_repetidos_es_400_y_no_borra(cal, cliente, monkeypatch):
    monkeypatch.setenv("CALENDARIO_PERMITIR_RESET", "1")
    alta(cal, "Previo", "2026-11-05T10:00")
    agenda = {"eventos": [{"id": "x", "titulo": "Uno", "inicio": "2026-11-05T10:00"},
                          {"id": "x", "titulo": "Dos", "inicio": "2026-11-06T10:00"}]}
    r = cliente.post("/api/reset", json=agenda)
    assert r.status_code == 400 and "repetido" in r.json()["error"]
    assert activos(cal) == {"Previo"}


def test_a03_reemplazar_es_atomico(cal):
    alta(cal, "Previo", "2026-11-05T10:00")
    ev = cal._preparar({"id": "x", "titulo": "Uno", "inicio": "2026-11-05T10:00"}, "importado")
    with pytest.raises(sqlite3.IntegrityError):
        cal.db.reemplazar([ev, dict(ev)])  # la base rechaza el id repetido
    assert activos(cal) == {"Previo"}  # el DELETE se ha deshecho


# ── A-04: booleanos en texto ──────────────────────────────────────────────

@pytest.mark.parametrize("valor,esperado", [
    (True, True), (False, False), (None, False), (1, True), (0, False),
    ("true", True), ("True", True), ("1", True), ("on", True), ("sí", True), ("si", True), ("yes", True),
    ("false", False), ("0", False), ("off", False), ("", False), ("no", False),
])
def test_a04_a_bool(valor, esperado):
    assert a_bool(valor) is esperado


@pytest.mark.parametrize("valor", ["false", "0", "off", ""])
def test_a04_api_alta_con_booleano_en_texto(cliente, valor):
    r = cliente.post("/api/eventos", json={"titulo": "Str", "inicio": "2026-11-04T10:00", "todo_el_dia": valor})
    assert r.status_code == 201 and r.json()["evento"]["todo_el_dia"] is False


def test_a04_api_edicion_y_reset_con_booleano_en_texto(cal, cliente, monkeypatch):
    ev = alta(cal, "Clase", "2026-11-04T10:00")
    r = cliente.patch(f"/api/eventos/{ev['id']}", json={"todo_el_dia": "false", "inicio": "2026-11-04T11:00"})
    assert r.status_code == 200
    assert r.json()["evento"]["todo_el_dia"] is False and r.json()["evento"]["fin"].startswith("2026-11-04T12:00")
    assert cliente.patch(f"/api/eventos/{ev['id']}", json={"todo_el_dia": "true"}).json()["evento"]["todo_el_dia"]
    monkeypatch.setenv("CALENDARIO_PERMITIR_RESET", "1")
    cliente.post("/api/reset", json={"eventos": [{"titulo": "R", "inicio": "2026-11-05T10:00", "todo_el_dia": "0"}]})
    assert cal.db.listar()[0]["todo_el_dia"] is False


@pytest.mark.parametrize("valor,esperado", [("false", False), ("0", False), ("off", False),
                                            ("on", True), ("1", True), ("true", True)])
def test_a04_formulario_web_con_booleano_en_texto(cal, cliente, valor, esperado):
    r = cliente.post("/ui/eventos", data={"titulo": "Form", "fecha": "2026-11-04", "hora": "10:00",
                                          "todo_el_dia": valor, "volver": "semana:2026-11-02"})
    assert r.status_code == 200
    assert cal.db.listar()[0]["todo_el_dia"] is esperado


# ── A-05: PATCH con id en el cuerpo ───────────────────────────────────────

def test_a05_patch_con_id_en_el_cuerpo_es_400(cal, cliente):
    ev = alta(cal, "Fisio", "2026-11-05T08:30")
    r = cliente.patch(f"/api/eventos/{ev['id']}", json={"id": "otro", "titulo": "Cambiado"})
    assert r.status_code == 400 and "id" in r.json()["error"]
    assert cal.obtener(ev["id"])["titulo"] == "Fisio"


# ── A-06: argumentos MCP con tipos inesperados ────────────────────────────

@pytest.mark.parametrize("texto,minutos", [
    (90, 90), ("90", 90), ("90 minutos", 90), ("45 min", 45), ("una hora", 60), ("1 hora", 60),
    ("hora y media", 90), ("una hora y media", 90), ("media hora", 30), ("dos horas", 120),
    ("un cuarto de hora", 15), ("tres cuartos de hora", 45), ("1,5 horas", 90), ("2h", 120),
    ("hora y cuarto", 75), ("mucho", None), ("", None), (None, None),
])
def test_a06_minutos_desde_texto(texto, minutos):
    assert minutos_desde_texto(texto) == minutos


@pytest.mark.parametrize("herramienta,argumentos,contiene", [
    ("crear_evento", {"titulo": "Café", "dia": "jueves", "hora": 10}, "a las 10:00"),
    ("crear_evento", {"titulo": "Café", "dia": "jueves", "hora": 10, "hora_fin": 12}, "a las 10:00"),
    ("crear_evento", {"titulo": "Café", "dia": "jueves", "hora": None}, "todo el día"),
    ("buscar_huecos", {"dia": "jueves", "duracion_min": "una hora"}, "tienes libre"),
    ("buscar_huecos", {"dia": "jueves", "duracion_min": "hora y media"}, "tienes libre"),
    ("buscar_huecos", {"dia": "jueves", "duracion_min": "90"}, "tienes libre"),
    ("buscar_huecos", {"dia": "jueves", "duracion_min": "un rato"}, "No entiendo la duración"),
    ("buscar_huecos", {"dia": "jueves", "duracion_min": None}, "No entiendo la duración"),
])
async def test_a06_herramientas_toleran_tipos(cal, herramienta, argumentos, contiene):
    async with Client(crear_servidor(cal)) as c:
        r = await c.call_tool(herramienta, argumentos)
    assert r.is_error is False, r.content
    assert r.structured_content["final"] is True
    assert contiene in r.structured_content["texto"]


async def test_a06_el_esquema_no_cambia(cal):
    async with Client(crear_servidor(cal)) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert tools["buscar_huecos"].input_schema["properties"]["duracion_min"]["type"] == "integer"
    for arg in ("hora", "hora_fin"):
        assert tools["crear_evento"].input_schema["properties"][arg]["type"] == "string"


# ── A-07: la API JSON exige application/json ──────────────────────────────

def test_a07_post_y_patch_sin_json_son_415(cal, cliente, monkeypatch):
    monkeypatch.setenv("CALENDARIO_PERMITIR_RESET", "1")
    cuerpo = b'{"titulo": "CSRF", "inicio": "2026-11-04T10:00"}'
    for ct in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x"):
        assert cliente.post("/api/eventos", content=cuerpo, headers={"content-type": ct}).status_code == 415, ct
        assert cliente.post("/api/reset", content=b'{"eventos": []}', headers={"content-type": ct}).status_code == 415
    assert cliente.post("/api/eventos", content=cuerpo).status_code == 415  # sin Content-Type
    ev = alta(cal, "Fisio", "2026-11-05T08:30")
    r = cliente.patch(f"/api/eventos/{ev['id']}", content=b'{"titulo": "x"}', headers={"content-type": "text/plain"})
    assert r.status_code == 415
    for accion in ("cancelar", "restaurar"):
        r = cliente.post(f"/api/eventos/{ev['id']}/{accion}", content=b"x=1",
                         headers={"content-type": "application/x-www-form-urlencoded"})
        assert r.status_code == 415, accion
    assert activos(cal) == {"Fisio"}
    # con JSON (también con charset) sigue funcionando; cancelar sin cuerpo, también
    r = cliente.post("/api/eventos", content=cuerpo, headers={"content-type": "application/json; charset=utf-8"})
    assert r.status_code == 201
    assert cliente.post(f"/api/eventos/{ev['id']}/cancelar").status_code == 200


def test_a07_los_formularios_de_la_ui_siguen_funcionando(cal, cliente):
    r = cliente.post("/ui/eventos", data={"titulo": "Desde la UI", "fecha": "2026-11-04", "hora": "10:00"})
    assert r.status_code == 200 and activos(cal) == {"Desde la UI"}
    ev = cal.db.listar()[0]
    assert cliente.post(f"/ui/eventos/{ev['id']}", data={"titulo": "Editado", "fecha": "2026-11-04",
                                                         "hora": "11:00"}).status_code == 200
    assert cliente.post(f"/ui/eventos/{ev['id']}/cancelar", data={}).status_code == 200
    assert cliente.post(f"/ui/eventos/{ev['id']}/restaurar", data={}).status_code == 200
    assert activos(cal) == {"Editado"}


# ── /ui ───────────────────────────────────────────────────────────────────

def test_ui_responde_200(cal, cliente):
    alta(cal, "Fisio", "2026-11-05T08:30")
    for ruta in ("/ui", "/ui/mes", "/ui?semana=2026-11-09", "/ui/mes?mes=2026-12"):
        assert cliente.get(ruta).status_code == 200, ruta

"""Regresiones del staging del Incremento 1 (QA del 5-7/11/2026, pruebas-INC1-staging.md).

Reloj fijo: sábado 7/11/2026 20:00 en Madrid, el día en que se pasaron las
pruebas. Agenda de prueba como la de §1.3 del documento de QA.
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from mcp import Client

from app import fechas
from app.db import BaseDatos
from app.dominio import Calendario
from app.main import META, crear_servidor

MADRID = ZoneInfo("Europe/Madrid")
AHORA = datetime(2026, 11, 7, 20, 0, tzinfo=MADRID)  # sábado


@pytest.fixture
def cal(tmp_path):
    c = Calendario(BaseDatos(tmp_path / "calendario.db"), MADRID, reloj=lambda: AHORA)
    c.reset([
        {"titulo": "Yoga", "inicio": "2026-11-08T19:00", "fin": "2026-11-08T20:00"},
        {"titulo": "Clase de inglés", "inicio": "2026-11-09T18:00", "fin": "2026-11-09T19:30"},
        {"titulo": "Revisión ITV", "inicio": "2026-11-09T10:00"},
        {"titulo": "Peluquería", "inicio": "2026-11-16T17:00"},
        {"titulo": "Peluquería (tinte)", "inicio": "2026-11-23T17:00"},
        {"titulo": "Comida familiar", "inicio": "2026-11-14", "todo_el_dia": True},
    ])
    return c


def activos(cal):
    return {e["titulo"] for e in cal.db.listar()}


# ── Cancelar con un día inventado por el modelo (CH-13, CH-14, CH-17) ────────

def test_ch13_dia_inventado_no_impide_encontrar_el_evento(cal):
    r = cal.cancelar("clase de inglés", "único")
    # No cancela (el día no se entiende), pero propone el evento correcto.
    assert "Clase de inglés" in activos(cal)
    assert r["texto"].endswith("?") and "'Clase de inglés' el lunes 9" in r["texto"]


def test_ch13_sin_dia_cancela_y_dice_que_se_puede_restaurar(cal):
    r = cal.cancelar("clase de inglés")
    assert "Clase de inglés" not in activos(cal)
    assert "restaurarlo" in r["texto"]


def test_ch14_ambigua_con_dia_inventado_lista_las_dos(cal):
    r = cal.cancelar("peluqueria", "único")
    assert {"Peluquería", "Peluquería (tinte)"} <= activos(cal)
    assert "'Peluquería' el lunes 16" in r["texto"] and "'Peluquería (tinte)' el lunes 23" in r["texto"]


def test_ch16_del_tinte_cancela_solo_esa(cal):
    cal.cancelar("peluquería del tinte")
    assert "Peluquería" in activos(cal) and "Peluquería (tinte)" not in activos(cal)


def test_ch17_inexistente_con_dia_inventado(cal):
    r = cal.cancelar("notario", "séptimo")
    assert r["texto"] == "No entiendo la fecha 'séptimo' y no encuentro ningún evento 'notario'. No he cancelado nada."
    assert len(activos(cal)) == 6


# ── Fecha imposible (CH-07) ──────────────────────────────────────────────────

def test_ch07_fecha_imposible_no_crea_y_lo_explica(cal):
    r = cal.crear("Revisión anual", "31 de noviembre")
    assert r["texto"].startswith("El 31 de noviembre no existe: noviembre tiene 30 días. No he creado nada.")
    assert len(activos(cal)) == 6


def test_fecha_imposible():
    assert fechas.fecha_imposible("el 31 de noviembre") == (31, "noviembre", 30)
    assert fechas.fecha_imposible("30 de noviembre") is None
    assert fechas.fecha_imposible("viernes") is None


# ── Solapamiento (CH-05): lo avisa el módulo; con DEV-07 llega tal cual ─────

def test_ch05_avisa_del_solapamiento(cal):
    r = cal.crear("Pádel", "mañana", "19:30")
    assert "Ojo: se solapa con 'Yoga' a las 19:00." in r["texto"]


# ── editar_evento (D4, CH-19) ────────────────────────────────────────────────

def test_ch19_mover_al_jueves_conserva_hora_y_duracion(cal):
    r = cal.editar_por_chat("yoga", "mañana", nuevo_dia="jueves")
    ev = r["datos"]["movido"]
    assert ev["inicio"] == "2026-11-12T19:00:00+01:00" and ev["fin"] == "2026-11-12T20:00:00+01:00"
    assert r["texto"] == "Hecho: ahora es 'Yoga' el jueves 12 a las 19:00."
    assert len(activos(cal)) == 6  # no crea otro


def test_editar_cambia_la_hora_y_conserva_la_duracion(cal):
    r = cal.editar_por_chat("inglés", nueva_hora="17:00")
    ev = r["datos"]["movido"]
    assert ev["inicio"].startswith("2026-11-09T17:00") and ev["fin"].startswith("2026-11-09T18:30")


def test_editar_avisa_del_solapamiento(cal):
    r = cal.editar_por_chat("itv", nuevo_dia="mañana", nueva_hora="19:30")
    assert "Ojo: se solapa con 'Yoga'" in r["texto"]


def test_editar_dia_completo_a_otro_dia(cal):
    ev = cal.editar_por_chat("comida familiar", nuevo_dia="domingo")["datos"]["movido"]
    assert ev["todo_el_dia"] is True and ev["inicio"].startswith("2026-11-08")


def test_editar_ambiguo_no_toca_nada(cal):
    r = cal.editar_por_chat("peluquería", nuevo_dia="viernes")
    assert r["texto"].endswith("¿Cuál muevo?") and r["datos"]["movido"] is None
    assert {e["inicio"][:10] for e in cal.db.listar() if e["titulo"].startswith("Peluquería")} == \
        {"2026-11-16", "2026-11-23"}


def test_editar_sin_cambios_pregunta(cal):
    r = cal.editar_por_chat("yoga")
    assert r["texto"].startswith("¿Qué quieres cambiar")


def test_editar_renombrar(cal):
    ev = cal.editar_por_chat("itv", nuevo_titulo="ITV del coche")["datos"]["movido"]
    assert ev["titulo"] == "ITV del coche" and ev["inicio"].startswith("2026-11-09T10:00")


async def test_editar_evento_por_mcp(cal):
    async with Client(crear_servidor(cal)) as c:
        r = await c.call_tool("editar_evento", {"titulo": "yoga", "nuevo_dia": "jueves", "nueva_hora": 18})
    assert r.structured_content["final"] is True
    assert r.structured_content["datos"]["movido"]["inicio"].startswith("2026-11-12T18:00")


# ── Metadatos para el core (DA-15) ───────────────────────────────────────────

async def test_las_herramientas_publican_sus_metadatos(cal):
    async with Client(crear_servidor(cal)) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    for nombre, meta in META.items():
        assert tools[nombre].meta == meta, nombre
        for patron in meta["mortimer"].get("requiere", []) + meta["mortimer"].get("excluye", []):
            re.compile(patron)


def _encaja(herramienta, clave, texto):
    return any(re.search(p, fechas.normalizar(texto)) for p in META[herramienta]["mortimer"].get(clave, []))


@pytest.mark.parametrize("texto", ["Mándale un WhatsApp a mi madre", "Recomiéndame una serie",
                                   "Pasa el yoga de mañana al jueves"])
def test_crear_excluye_lo_que_no_es_crear(texto):
    assert _encaja("crear_evento", "excluye", texto)


@pytest.mark.parametrize("texto", ["Apúntame comida con Laura el miércoles a las dos y media",
                                   "Tengo el veterinario pasado mañana a las 9",
                                   "quedo con Pau el sabdo a mediodia",
                                   "Pon que el domingo es el cumpleaños de Jordi"])
def test_crear_no_excluye_altas(texto):
    assert not _encaja("crear_evento", "excluye", texto)


@pytest.mark.parametrize("texto,si", [
    ("¿Dónde me cabe una hora de estudio pasado mañana?", True),
    ("¿Tengo libres tres horas seguidas el lunes?", True),
    ("¿Tengo un rato libre hoy?", True),
    ("quedo con Pau el sabdo a mediodia", False),
    ("Recomiéndame una serie para ver el fin de semana", False),
])
def test_huecos_solo_si_se_pregunta_por_tiempo_libre(texto, si):
    assert _encaja("buscar_huecos", "requiere", texto) is si


@pytest.mark.parametrize("texto,si", [
    ("Anula la clase de inglés", True), ("Cancela la peluquería", True),
    ("Borra la cita con el notario", True), ("Quítame el yoga", True),
    ("¿Qué tengo mañana?", False), ("Hola, ¿qué tal?", False),
])
def test_cancelar_solo_si_se_pide(texto, si):
    assert _encaja("cancelar_evento", "requiere", texto) is si


# ── DA-17 (F1.0): el core despierta el módulo solo si el mensaje va con él ──

_CHARLA = ["hola mortimer", "que he mencionado?", "esta alucinando", "Hola, ¿qué tal?",
           "¿qué tal el día?", "nada, aquí aburrido", "¿Qué sabes hacer?", "Pon música relajante",
           "Explícame qué es el protocolo MCP", "gracias!", "me llamo Enric", "cuéntame un chiste"]


@pytest.mark.parametrize("texto", _CHARLA)
def test_la_charla_no_despierta_el_calendario(texto):
    for herramienta, meta in META.items():
        requiere = meta["mortimer"].get("requiere")
        assert requiere, f"{herramienta} debe declarar requiere (DA-17)"
        assert not _encaja(herramienta, "requiere", texto), (herramienta, texto)


@pytest.mark.parametrize("texto", [
    "Apúntame comida con Laura el miércoles a las dos y media",
    "Tengo el veterinario pasado mañana a las 9",
    "El viernes tengo tutoría de 11 a 12 en la facultad",
    "Pon que el domingo es el cumpleaños de Jordi",
    "Ponme una cita con el médico",
    "Añade una reunión con Marc el 20 de noviembre a las 10 en la sala 3",
    "quedo con Pau el sabdo a mediodia",
    "Apúntame algo para el jueves",
])
def test_las_altas_despiertan_crear_evento(texto):
    assert _encaja("crear_evento", "requiere", texto)


@pytest.mark.parametrize("texto", [
    "¿Qué planes tengo para mañana?", "¿Qué tengo esta semana?", "enséñame la agenda",
    "¿Cuándo es la cena con Marta?", "¿tengo algo el finde?", "que tengo la smana que viene",
])
def test_las_consultas_despiertan_listar_eventos(texto):
    assert _encaja("listar_eventos", "requiere", texto)

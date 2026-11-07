"""Pruebas del servidor MCP del Calendario (DEV-05), en memoria y sin Ollama."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from mcp import Client

from app.db import BaseDatos
from app.dominio import Calendario
from app.main import crear_servidor, reloj_desde_entorno, servicio_desde_entorno

MADRID = ZoneInfo("Europe/Madrid")
AHORA = datetime(2026, 11, 3, 9, 10, tzinfo=MADRID)
RAIZ = Path(__file__).resolve().parents[3]

# Contrato §3.2 del plan del 30/10: argumentos y cuáles son obligatorios.
CONTRATO = {
    "crear_evento": ({"titulo", "dia", "hora", "hora_fin", "ubicacion"}, {"titulo", "dia"}),
    "listar_eventos": ({"periodo"}, {"periodo"}),
    "cancelar_evento": ({"titulo", "dia", "id"}, set()),
    "buscar_huecos": ({"dia", "duracion_min"}, {"dia", "duracion_min"}),
}


@pytest.fixture
def servicio(tmp_path):
    return Calendario(BaseDatos(tmp_path / "calendario.db"), MADRID, reloj=lambda: AHORA)


@pytest.fixture
def servidor(servicio):
    return crear_servidor(servicio)


async def test_lista_las_cuatro_herramientas_del_contrato(servidor):
    async with Client(servidor) as c:
        assert c.server_info.name == "calendario"
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert set(tools) == set(CONTRATO)
    for nombre, (argumentos, obligatorios) in CONTRATO.items():
        esquema = tools[nombre].input_schema
        assert set(esquema["properties"]) == argumentos, nombre
        assert set(esquema.get("required", [])) == obligatorios, nombre
        assert tools[nombre].description.strip(), nombre
        for arg, prop in esquema["properties"].items():
            assert prop.get("description", "").strip(), f"{nombre}.{arg} sin descripción"
        assert set(tools[nombre].output_schema["properties"]) == {"texto", "final", "datos"}
    assert tools["buscar_huecos"].input_schema["properties"]["duracion_min"]["type"] == "integer"


async def test_descripciones_del_checkpoint(servidor):
    async with Client(servidor) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert "tengo dentista el martes" in tools["crear_evento"].description
    assert "explícitamente" in tools["buscar_huecos"].description
    dia = tools["crear_evento"].input_schema["properties"]["dia"]["description"]
    lista = dia.split("palabras:")[1].split(";")[0]
    for palabra in ("hoy", "manana", "pasado manana", "miercoles", "sabado", "domingo"):
        assert palabra in lista
    assert not any(c in lista for c in "áéíóúñ")  # lista cerrada sin tildes


@pytest.mark.parametrize("herramienta,argumentos", [
    ("crear_evento", {"titulo": "Dentista", "dia": "jueves", "hora": "16:00"}),
    ("crear_evento", {"titulo": "Dentista", "dia": "nunca jamás"}),
    ("listar_eventos", {"periodo": "esta semana"}),
    ("cancelar_evento", {"titulo": "notario"}),
    ("buscar_huecos", {"dia": "mañana", "duracion_min": 60}),
    ("buscar_huecos", {"dia": "mañana", "duracion_min": "30"}),  # el modelo a veces manda texto
])
async def test_cada_herramienta_devuelve_texto_y_final(servidor, herramienta, argumentos):
    async with Client(servidor) as c:
        r = await c.call_tool(herramienta, argumentos)
    assert r.is_error is False
    sc = r.structured_content
    assert isinstance(sc["texto"], str) and sc["texto"]
    assert sc["final"] is True and isinstance(sc["datos"], dict)
    # el contenido de texto es la misma frase para el usuario
    assert [c.text for c in r.content] == [sc["texto"]]


async def test_flujo_completo_por_mcp(servidor, servicio):
    async with Client(servidor) as c:
        await c.call_tool("crear_evento", {"titulo": "Dentista", "dia": "jueves", "hora": "16:00"})
        await c.call_tool("crear_evento", {"titulo": "Dentista", "dia": "viernes", "hora": "10:00"})
        r = await c.call_tool("cancelar_evento", {"titulo": "dentista"})
        assert r.structured_content["datos"]["cancelado"] is None  # ambiguo: no cancela
        assert len(servicio.listar_rango()) == 2
        r = await c.call_tool("cancelar_evento", {"titulo": "dentista", "dia": "viernes"})
        assert r.structured_content["datos"]["cancelado"]["titulo"] == "Dentista"
        r = await c.call_tool("listar_eventos", {"periodo": "esta semana"})
    assert r.structured_content["texto"] == "Jueves 5: 16:00 Dentista"


def test_reloj_desde_entorno(monkeypatch):
    assert reloj_desde_entorno(MADRID, "2026-10-29T18:00")() == datetime(2026, 10, 29, 18, 0, tzinfo=MADRID)
    assert reloj_desde_entorno(MADRID, "2026-10-29T17:00:00Z")().hour == 18
    monkeypatch.delenv("MORTIMER_AHORA", raising=False)
    real = reloj_desde_entorno(MADRID)()
    assert real.tzinfo is not None and abs((datetime.now(MADRID) - real).total_seconds()) < 5


def test_servicio_desde_entorno(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "datos"))
    monkeypatch.setenv("MORTIMER_AHORA", "2026-10-29T18:00")
    monkeypatch.setenv("HUECOS_DESDE", "09:00")
    s = servicio_desde_entorno()
    assert s.ahora() == datetime(2026, 10, 29, 18, 0, tzinfo=MADRID)
    assert s.huecos_desde.hour == 9
    s.crear("Prueba", "mañana", "10:00")
    assert (tmp_path / "datos" / "calendario.db").exists()


# ── Integración con el core (sin modificarlo) ──────────────────────────────

def cargar_registro_del_core():
    """core/app/registro.py se carga por ruta: el core y el módulo se llaman `app` los dos."""
    ruta = RAIZ / "core" / "app" / "registro.py"
    spec = importlib.util.spec_from_file_location("core_registro", ruta)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["core_registro"] = mod  # las dataclasses lo buscan en sys.modules
    spec.loader.exec_module(mod)
    return mod


def cargar_modulo_calendario(alias: str = "modulo_calendario"):
    """Carga modules/calendario/app como paquete con otro nombre.

    Receta para quien necesite el módulo en memoria junto al core (p. ej. el
    benchmark con --esquema modulo): main.py usa importaciones relativas.
    """
    carpeta = RAIZ / "modules" / "calendario" / "app"
    spec = importlib.util.spec_from_file_location(alias, carpeta / "__init__.py",
                                                  submodule_search_locations=[str(carpeta)])
    paquete = importlib.util.module_from_spec(spec)
    sys.modules[alias] = paquete
    spec.loader.exec_module(paquete)
    return importlib.import_module(f"{alias}.main")


async def test_el_registro_del_core_descubre_el_modulo(servidor):
    registro_mod = cargar_registro_del_core()
    registro = registro_mod.Registro([servidor])
    await registro.actualizar()

    assert registro.modulos[0].nombre == "calendario" and registro.modulos[0].estado == "ok"
    herramientas = registro.para_llm()
    assert len(herramientas) == 4
    assert {h["function"]["name"] for h in herramientas} == set(CONTRATO)
    crear = next(h for h in herramientas if h["function"]["name"] == "crear_evento")
    assert set(crear["function"]["parameters"]["required"]) == {"titulo", "dia"}

    modulo, r = await registro.invocar("crear_evento", {"titulo": "Pádel", "dia": "viernes", "hora": "19:00"})
    assert modulo == "calendario" and r.ok
    assert r.datos["final"] is True and r.texto == r.datos["texto"]


async def test_receta_de_carga_en_memoria(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    main = cargar_modulo_calendario()
    registro = cargar_registro_del_core().Registro([main.mcp])
    await registro.actualizar()
    assert len(registro.para_llm()) == 4

"""DEV-07 (DA-11): si el módulo redacta la respuesta (final=True), no hay segunda ronda.
DA-15: reparación de la expresión temporal y filtros de intención declarados por el módulo.

Servidor MCP de prueba definido aquí, con las mismas convenciones que el Calendario.
"""

from __future__ import annotations

from typing import Any

import pytest
from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent

from app.llm import LlamadaHerramienta, RespuestaLLM
from app.memoria import Memoria
from app.orquestador import Orquestador
from app.registro import Registro

from test_bucle import LLMGuion

AHORA = "2026-11-07T20:00"  # sábado


def servidor_de_prueba(llamadas: list[dict[str, Any]]) -> MCPServer:
    mcp = MCPServer("agenda", version="0.0.1")

    def final(texto: str) -> CallToolResult:
        r = {"texto": texto, "final": True, "datos": {}}
        return CallToolResult(content=[TextContent(type="text", text=texto)], structured_content=r)

    @mcp.tool(meta={"mortimer": {"fechas": {"dia": "dia"}, "excluye": [r"\bwhatsapp\b"]}})
    def apuntar(titulo: str, dia: str) -> CallToolResult:
        """Apunta algo."""
        llamadas.append({"apuntar": {"titulo": titulo, "dia": dia}})
        return final(f"Hecho: {titulo} ({dia}).")

    @mcp.tool(meta={"mortimer": {"fechas": {"dia": "dia"}, "requiere": [r"\b(cancel\w*|anul\w*)\b"]}})
    def quitar(titulo: str = "", dia: str = "") -> CallToolResult:
        """Quita algo."""
        llamadas.append({"quitar": {"titulo": titulo, "dia": dia}})
        return final(f"Quitado: {titulo} ({dia or 'sin día'}).")

    @mcp.tool(meta={"mortimer": {"fechas": {"periodo": "periodo"}}})
    def consultar(periodo: str) -> CallToolResult:
        """Consulta."""
        llamadas.append({"consultar": {"periodo": periodo}})
        return final(f"Nada {periodo}.")

    @mcp.tool()
    def no_final(x: str = "") -> str:
        """Devuelve texto sin final."""
        return "dato crudo"

    @mcp.tool()
    def falla(x: str = "") -> CallToolResult:
        """Falla siempre."""
        return CallToolResult(content=[TextContent(type="text", text="roto")], is_error=True)

    return mcp


@pytest.fixture
def memoria(tmp_path):
    return Memoria(tmp_path / "t.db")


@pytest.fixture
def llamadas():
    return []


@pytest.fixture
async def registro(llamadas):
    r = Registro([servidor_de_prueba(llamadas)])
    await r.actualizar()
    return r


def una(nombre, **argumentos):
    return RespuestaLLM("", [LlamadaHerramienta(nombre, argumentos)], {"role": "assistant", "content": ""})


def orq(llm, registro, memoria):
    return Orquestador(llm, registro, memoria, ahora_fijo=AHORA)


# ── DEV-07 ───────────────────────────────────────────────────────────────────

async def test_herramienta_final_una_sola_ronda(registro, memoria):
    llm = LLMGuion([una("apuntar", titulo="Yoga", dia="manana")])
    r = await orq(llm, registro, memoria).responder("apunta yoga mañana")
    assert r.texto == "Hecho: Yoga (manana)."
    assert len(llm.recibido) == 1
    fila = memoria.interacciones()[0]
    assert fila["rondas_llm"] == 1 and fila["respuesta_modulo"] == 1
    assert fila["t_decision_ms"] is not None


async def test_herramienta_no_final_hace_segunda_ronda(registro, memoria):
    llm = LLMGuion([una("no_final"), RespuestaLLM("Te lo resumo.")])
    r = await orq(llm, registro, memoria).responder("dame el dato")
    assert r.texto == "Te lo resumo." and len(llm.recibido) == 2
    assert memoria.interacciones()[0]["respuesta_modulo"] == 0


async def test_herramienta_con_error_vuelve_al_llm(registro, memoria):
    llm = LLMGuion([una("falla"), RespuestaLLM("No he podido.")])
    r = await orq(llm, registro, memoria).responder("haz algo")
    assert r.texto == "No he podido."
    assert llm.recibido[1][0][-1] == {"role": "tool", "tool_name": "falla", "content": "roto"}


async def test_la_respuesta_del_modulo_queda_en_el_historial(registro, memoria):
    llm = LLMGuion([una("apuntar", titulo="Yoga", dia="manana"), RespuestaLLM("Vale.")])
    o = orq(llm, registro, memoria)
    r1 = await o.responder("apunta yoga mañana")
    await o.responder("gracias", r1.conversacion)
    assert any(m["content"] == "Hecho: Yoga (manana)." for m in llm.recibido[1][0])


# ── DA-15: reparación de fechas ──────────────────────────────────────────────

@pytest.mark.parametrize("texto,del_modelo,esperado", [
    ("Tengo el veterinario pasado mañana a las 9", "manana", "pasado manana"),      # CH-03 / CH-22
    ("Apúntame comida con Laura el miércoles a las dos y media", "miñero", "miercoles"),  # CH-01
    ("apunta dentsita el juevs a las 5 de la tarde", "jueves", "jueves"),
    ("Apunta la revisión el 20 de noviembre", "20/11", "20 de noviembre"),
])
async def test_repara_el_dia(registro, memoria, llamadas, texto, del_modelo, esperado):
    llm = LLMGuion([una("apuntar", titulo="X", dia=del_modelo)])
    r = await orq(llm, registro, memoria).responder(texto)
    assert llamadas[-1]["apuntar"]["dia"] == esperado
    if del_modelo != esperado:
        assert r.herramientas[0]["argumentos_modelo"]["dia"] == del_modelo


async def test_repara_el_periodo_con_errata(registro, memoria, llamadas):  # CH-27
    llm = LLMGuion([una("consultar", periodo="el mes que viene")])
    await orq(llm, registro, memoria).responder("que tengo la smana que viene")
    assert llamadas[-1]["consultar"]["periodo"] == "la semana que viene"


async def test_quita_el_dia_inventado_si_es_opcional(registro, memoria, llamadas):  # CH-13/14
    llm = LLMGuion([una("quitar", titulo="clase de inglés", dia="sábado")])
    await orq(llm, registro, memoria).responder("Anula la clase de inglés")
    assert llamadas[-1]["quitar"] == {"titulo": "clase de inglés", "dia": ""}


async def test_no_toca_el_dia_obligatorio_sin_expresion(registro, memoria, llamadas):
    llm = LLMGuion([una("apuntar", titulo="Algo", dia="jueves")])
    await orq(llm, registro, memoria).responder("apúntame algo")
    assert llamadas[-1]["apuntar"]["dia"] == "jueves"


async def test_varias_expresiones_respeta_al_modelo(registro, memoria, llamadas):
    llm = LLMGuion([una("apuntar", titulo="Yoga", dia="jueves")])
    await orq(llm, registro, memoria).responder("el yoga de mañana apúntalo también el jueves")
    assert llamadas[-1]["apuntar"]["dia"] == "jueves"


async def test_franja_por_la_manana_no_es_manana(registro, memoria, llamadas):
    llm = LLMGuion([una("apuntar", titulo="Gestoría", dia="manana")])
    await orq(llm, registro, memoria).responder("Apunta gestoría el viernes por la mañana")
    assert llamadas[-1]["apuntar"]["dia"] == "viernes"


async def test_lunes_16_se_resuelve_a_fecha(registro, memoria, llamadas):
    llm = LLMGuion([una("quitar", titulo="peluquería", dia="lunes")])
    o = orq(llm, registro, memoria)
    await o.responder("cancela la peluquería del lunes 16")
    assert llamadas[-1]["quitar"]["dia"] == "2026-11-16"


# ── DA-15: filtros de intención ──────────────────────────────────────────────

async def test_excluye_rechaza_y_quita_la_herramienta(registro, memoria, llamadas):  # CH-29
    llm = LLMGuion([una("apuntar", titulo="WhatsApp a mi madre", dia="séptimo"),
                    RespuestaLLM("No puedo mandar mensajes.")])
    r = await orq(llm, registro, memoria).responder("Mándale un WhatsApp a mi madre")
    assert llamadas == []
    assert r.texto == "No puedo mandar mensajes."
    assert r.herramientas[0]["ok"] is False and r.herramientas[0]["rechazada"]
    # DA-17: "apuntar" ya no se ofrece ni en la primera ronda (su `excluye`
    # encaja), y en la segunda sigue fuera.
    assert all("apuntar" not in {h["function"]["name"] for h in ofrecidas}
               for _, ofrecidas in llm.recibido)


async def test_requiere_usa_tambien_el_mensaje_anterior(registro, memoria, llamadas):  # CH-15
    llm = LLMGuion([RespuestaLLM("¿Cuál de las dos?"), una("quitar", titulo="tinte")])
    o = orq(llm, registro, memoria)
    r1 = await o.responder("Cancela la peluquería")
    r2 = await o.responder("La del tinte", r1.conversacion)
    assert llamadas[-1]["quitar"]["titulo"] == "tinte" and r2.texto.startswith("Quitado")


async def test_requiere_rechaza_sin_peticion(registro, memoria, llamadas):
    llm = LLMGuion([una("quitar", titulo="hola"), RespuestaLLM("¡Hola!")])
    r = await orq(llm, registro, memoria).responder("Hola, ¿qué tal?")
    assert llamadas == [] and r.texto == "¡Hola!"


# ── DEV-10: reloj fijo ───────────────────────────────────────────────────────

async def test_reloj_fijo_en_el_contexto_temporal(memoria):
    registro = Registro([])
    await registro.actualizar()
    llm = LLMGuion([RespuestaLLM("ok")])
    await Orquestador(llm, registro, memoria, ahora_fijo="2026-10-29T18:00").responder("hola")
    assert llm.recibido[0][0][-1]["content"] == "hola"
    assert llm.recibido[0][0][-2]["content"].startswith("Ahora es jueves 29/10/2026, 18:00 ")


def test_interacciones_desde_id(memoria):
    for i in range(3):
        memoria.registrar(conversacion="c", canal="api", modelo="m", peticion=str(i), respuesta="",
                          herramientas=[], rondas_llm=1, t_llm_ms=0, t_modulos_ms=0, t_total_ms=0)
    assert [f["peticion"] for f in memoria.interacciones(desde_id=1)] == ["2", "1"]


def test_migra_columnas_de_evaluacion(tmp_path):
    import sqlite3
    ruta = tmp_path / "vieja.db"
    db = sqlite3.connect(ruta)
    db.execute("CREATE TABLE interacciones (id INTEGER PRIMARY KEY, fecha TEXT, conversacion TEXT, "
               "canal TEXT, modelo TEXT, peticion TEXT, respuesta TEXT, modulo TEXT, herramientas TEXT, "
               "rondas_llm INTEGER, t_llm_ms REAL, t_modulos_ms REAL, t_total_ms REAL, error TEXT)")
    db.commit(); db.close()
    cols = {f["name"] for f in Memoria(ruta)._db.execute("PRAGMA table_info(interacciones)")}
    assert {"truncada", "t_decision_ms", "respuesta_modulo"} <= cols

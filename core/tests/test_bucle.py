"""Pruebas del Incremento 0 sin Ollama: el LLM se sustituye por un guion fijo
y el módulo de prueba se conecta en memoria (mismo protocolo MCP, sin red).

Criterio de terminado del incremento:
  - el núcleo conversa sin módulos instalados (RF-01)
  - enruta una petición al módulo de prueba (RF-02)
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.llm import LlamadaHerramienta, RespuestaLLM
from app.memoria import Memoria
from app.orquestador import Orquestador
from app.registro import Registro

RAIZ = Path(__file__).resolve().parents[2]


def cargar_modulo_prueba():
    ruta = RAIZ / "modules" / "prueba" / "app" / "main.py"
    spec = importlib.util.spec_from_file_location("modulo_prueba", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.mcp


class LLMGuion:
    """Devuelve respuestas predefinidas y guarda lo que recibe."""

    modelo = "guion"

    def __init__(self, respuestas: list[RespuestaLLM]):
        self.respuestas = list(respuestas)
        self.recibido: list[tuple[list, list]] = []

    async def chat(self, mensajes, herramientas):
        self.recibido.append((list(mensajes), herramientas))
        return self.respuestas.pop(0)


@pytest.fixture
def memoria(tmp_path):
    return Memoria(tmp_path / "test.db")


async def test_conversa_sin_modulos(memoria):
    registro = Registro([])
    await registro.actualizar()
    llm = LLMGuion([RespuestaLLM("¡Hola! Soy Mortimer.")])
    orq = Orquestador(llm, registro, memoria)

    r = await orq.responder("hola")

    assert r.texto == "¡Hola! Soy Mortimer."
    assert r.herramientas == []
    assert llm.recibido[0][1] == []  # no se ofrecen herramientas
    fila = memoria.interacciones()[0]
    assert fila["modulo"] is None and fila["error"] is None


async def test_descubre_herramientas_del_modulo():
    registro = Registro([cargar_modulo_prueba()])
    await registro.actualizar()

    assert registro.modulos[0].nombre == "prueba"
    assert registro.modulos[0].estado == "ok"
    tool = registro.para_llm()[0]
    assert tool["function"]["name"] == "dime_la_hora"
    assert "zona" in tool["function"]["parameters"]["properties"]


async def test_enruta_al_modulo_de_prueba(memoria):
    registro = Registro([cargar_modulo_prueba()])
    await registro.actualizar()
    llm = LLMGuion([
        RespuestaLLM("", [LlamadaHerramienta("dime_la_hora", {"zona": "Europe/Madrid"})],
                     {"role": "assistant", "content": "", "tool_calls": []}),
        RespuestaLLM("Son las doce."),
    ])
    orq = Orquestador(llm, registro, memoria)

    r = await orq.responder("¿qué hora es?")

    assert r.texto == "Son las doce."
    assert r.herramientas[0]["modulo"] == "prueba"
    assert r.herramientas[0]["ok"] is True
    # el resultado real del módulo vuelve al LLM como mensaje de herramienta
    ultimo = llm.recibido[1][0][-1]
    assert ultimo["role"] == "tool" and "Europe/Madrid" in ultimo["content"]
    fila = memoria.interacciones()[0]
    assert fila["modulo"] == "prueba" and fila["rondas_llm"] == 2


async def test_herramienta_inexistente_no_rompe(memoria):
    registro = Registro([])
    await registro.actualizar()
    llm = LLMGuion([
        RespuestaLLM("", [LlamadaHerramienta("borrar_todo", {})]),
        RespuestaLLM("No puedo hacer eso."),
    ])
    r = await Orquestador(llm, registro, memoria).responder("borra todo")
    assert r.herramientas[0]["ok"] is False
    assert r.texto == "No puedo hacer eso."


async def test_modulo_caido_no_impide_arrancar():
    registro = Registro(["http://127.0.0.1:1"])  # puerto sin servidor
    await registro.actualizar()
    assert registro.modulos[0].estado == "error"
    assert registro.herramientas == {}


async def test_limite_de_rondas(memoria):
    registro = Registro([cargar_modulo_prueba()])
    await registro.actualizar()
    bucle = RespuestaLLM("", [LlamadaHerramienta("dime_la_hora", {})])
    llm = LLMGuion([bucle, bucle, RespuestaLLM("Vale.")])
    r = await Orquestador(llm, registro, memoria, max_rondas=2).responder("hora")
    assert r.texto == "Vale."
    assert llm.recibido[2][1] == []  # en la última ronda ya no hay herramientas


async def test_historial_por_conversacion(memoria):
    registro = Registro([])
    await registro.actualizar()
    llm = LLMGuion([RespuestaLLM("Encantado, Enric."), RespuestaLLM("Te llamas Enric.")])
    orq = Orquestador(llm, registro, memoria)
    r1 = await orq.responder("me llamo Enric")
    await orq.responder("¿cómo me llamo?", r1.conversacion)
    contenidos = [m["content"] for m in llm.recibido[1][0]]
    assert any(c.endswith("] me llamo Enric") for c in contenidos)


async def test_prompt_de_sistema_fijo_y_mensaje_sellado(memoria):
    """El prefijo (sistema) no cambia entre peticiones: Ollama puede reutilizar su caché."""
    registro = Registro([])
    await registro.actualizar()
    llm = LLMGuion([RespuestaLLM("a"), RespuestaLLM("b")])
    orq = Orquestador(llm, registro, memoria)
    await orq.responder("uno")
    await orq.responder("dos")
    assert llm.recibido[0][0][0] == llm.recibido[1][0][0]
    import re
    assert re.match(r"^\[\w+ \d{2}/\d{2}/\d{4} \d{2}:\d{2}\] dos$", llm.recibido[1][0][-1]["content"])

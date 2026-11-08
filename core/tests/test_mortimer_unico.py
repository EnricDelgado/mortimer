"""DA-17: Mortimer es uno solo y conversa; los módulos amplían lo que sabe hacer.

- F1.0: los módulos se despiertan solo si el mensaje tiene que ver con ellos,
  y la fecha no va pegada al mensaje del usuario.
- F1.2: el prompt lleva la lista de módulos instalados.
- F1.3 / F1.4: "No tengo ningún módulo para eso" → sugerencia y necesidad apuntada.

Sin Ollama: el LLM es un guion fijo y el módulo de prueba se conecta en memoria.
"""

from __future__ import annotations

import pytest

from app.llm import LlamadaHerramienta, RespuestaLLM
from app.memoria import Memoria
from app.orquestador import (FRASE_SIN_MODULO, SUGERENCIA, Orquestador, bloque_capacidades,
                             prompt_sistema)
from app.registro import InfoModulo, Registro

from test_bucle import LLMGuion, cargar_modulo_prueba
from test_frontend import chat, core  # noqa: F401  (fixture)


@pytest.fixture
def memoria(tmp_path):
    return Memoria(tmp_path / "test.db")


@pytest.fixture
async def registro():
    reg = Registro([cargar_modulo_prueba()])
    await reg.actualizar()
    return reg


def ofrecidas(llm, ronda=0):
    return {h["function"]["name"] for h in llm.recibido[ronda][1]}


# ── F1.2: catálogo de capacidades ───────────────────────────────────────────

async def test_el_prompt_lista_los_modulos_activos(registro):
    texto = prompt_sistema(registro)
    assert "Tus módulos instalados:\n- prueba: Módulo de prueba" in texto
    assert FRASE_SIN_MODULO in texto


def test_los_modulos_caidos_no_aparecen():
    reg = Registro([])
    reg.modulos = [InfoModulo("calendario", "1", "Agenda.", None, [], "ok"),
                   InfoModulo("http://musica:8001", "", "", None, [], "error", "caído")]
    bloque = bloque_capacidades(reg)
    assert "- calendario: Agenda." in bloque and "musica" not in bloque


def test_sin_modulos_solo_conversa():
    assert "No tienes ningún módulo instalado" in bloque_capacidades(Registro([]))


async def test_el_prompt_no_cambia_entre_peticiones(registro, memoria):
    llm = LLMGuion([RespuestaLLM("¡Hola!"), RespuestaLLM("Bien, ¿y tú?")])
    orq = Orquestador(llm, registro, memoria)
    r = await orq.responder("hola")
    await orq.responder("¿qué tal?", r.conversacion)
    assert llm.recibido[0][0][0] == llm.recibido[1][0][0]
    assert "calendario" not in llm.recibido[0][0][0]["content"].lower()


# ── F1.0: los módulos se despiertan solo cuando toca ───────────────────────

async def test_un_saludo_no_despierta_ningun_modulo(registro, memoria):
    llm = LLMGuion([RespuestaLLM("¡Hola! ¿Qué tal?")])
    r = await Orquestador(llm, registro, memoria).responder("hola mortimer")
    assert llm.recibido[0][1] == []          # el modelo no ve ninguna herramienta
    assert r.herramientas == []
    fila = memoria.interacciones()[0]
    assert fila["despiertas"] == [] and fila["sin_capacidad"] == 0


async def test_una_peticion_relacionada_despierta_su_modulo(registro, memoria):
    llm = LLMGuion([
        RespuestaLLM("", [LlamadaHerramienta("dime_la_hora", {})], {"role": "assistant", "content": ""}),
        RespuestaLLM("Son las doce."),
    ])
    await Orquestador(llm, registro, memoria).responder("¿qué hora es?")
    assert ofrecidas(llm) == {"dime_la_hora"}
    assert memoria.interacciones()[0]["despiertas"] == ["prueba"]


async def test_el_mensaje_anterior_solo_cuenta_si_mortimer_pregunto(registro, memoria):
    # Mortimer pregunta → la respuesta del usuario hereda el contexto del mensaje anterior.
    llm = LLMGuion([RespuestaLLM("¿De qué ciudad quieres la hora?"), RespuestaLLM("ok")])
    orq = Orquestador(llm, registro, memoria)
    r = await orq.responder("dime la hora")
    await orq.responder("de Tokio", r.conversacion)
    assert ofrecidas(llm, 1) == {"dime_la_hora"}

    # Mortimer no pregunta → el mensaje siguiente se juzga solo.
    llm = LLMGuion([RespuestaLLM("Son las doce."), RespuestaLLM("¡De nada!")])
    orq = Orquestador(llm, registro, memoria)
    r = await orq.responder("dime la hora")
    await orq.responder("gracias", r.conversacion)
    assert ofrecidas(llm, 1) == set()


async def test_historial_antiguo_con_sello_se_limpia(registro, memoria):
    memoria.guardar_mensaje("c1", "user", "[domingo 08/11/2026 12:01] hola mortimer")
    memoria.guardar_mensaje("c1", "assistant", "¡Hola!")
    llm = LLMGuion([RespuestaLLM("Nada en especial.")])
    await Orquestador(llm, registro, memoria).responder("¿qué te he dicho?", "c1")
    usuario = [m["content"] for m in llm.recibido[0][0] if m["role"] == "user"]
    assert usuario == ["hola mortimer", "¿qué te he dicho?"]


# ── F1.3 / F1.4: lo que no sabe hacer ──────────────────────────────────────

async def test_sin_modulo_sugiere_y_apunta_la_necesidad(registro, memoria):
    llm = LLMGuion([RespuestaLLM("No tengo ningún módulo para eso: haría falta uno de música.")])
    r = await Orquestador(llm, registro, memoria).responder("pon música relajante")
    assert r.texto.endswith(SUGERENCIA)
    fila = memoria.interacciones()[0]
    assert fila["sin_capacidad"] == 1
    (n,) = memoria.necesidades()
    assert n["peticion"] == "pon música relajante" and n["estado"] == "abierta"
    assert n["interaccion_id"] == fila["id"]


async def test_la_charla_no_apunta_necesidades(registro, memoria):
    llm = LLMGuion([RespuestaLLM("¡Hola! Aquí estoy.")])
    await Orquestador(llm, registro, memoria).responder("hola")
    assert memoria.necesidades() == []


async def test_si_el_modelo_ya_sugiere_no_se_repite(registro, memoria):
    llm = LLMGuion([RespuestaLLM(f"No tengo ningún módulo para eso. {SUGERENCIA}")])
    r = await Orquestador(llm, registro, memoria).responder("enciende la luz")
    assert r.texto.count(SUGERENCIA) == 1


async def test_borrar_la_conversacion_borra_sus_necesidades(registro, memoria):
    llm = LLMGuion([RespuestaLLM("No tengo ningún módulo para eso.")])
    r = await Orquestador(llm, registro, memoria).responder("manda un correo a Laura")
    assert len(memoria.necesidades()) == 1
    memoria.borrar_conversacion(r.conversacion)
    assert memoria.necesidades() == []


async def test_api_y_portada_de_necesidades(core):  # noqa: F811
    core.llm.respuestas.append(RespuestaLLM("No tengo ningún módulo para eso."))
    await chat(core, "pon música relajante")
    (n,) = (await core.get("/api/necesidades")).json()
    assert n["peticion"] == "pon música relajante"

    html = (await core.get("/")).text
    assert "Lo que aún no sé hacer" in html and "«pon música relajante»" in html

    r = await core.patch(f"/api/necesidades/{n['id']}", json={"estado": "descartada"})
    assert r.status_code == 200
    assert (await core.get("/api/necesidades", params={"estado": "abierta"})).json() == []
    assert (await core.patch("/api/necesidades/999", json={"estado": "cubierta"})).status_code == 404
    assert (await core.patch(f"/api/necesidades/{n['id']}", json={"estado": "otra"})).status_code == 422

    r = await core.post(f"/necesidades/{n['id']}/cubierta")
    assert r.status_code == 303
    assert (await core.get("/api/necesidades")).json()[0]["estado"] == "cubierta"

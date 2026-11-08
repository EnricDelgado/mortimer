"""Frontend del core: mapa de módulos, registro de conversaciones y chat flotante.

Sin Ollama: el LLM es un guion fijo y el módulo de prueba se conecta en memoria.
"""

from __future__ import annotations

import httpx
import pytest

from app.config import Config
from app.llm import LlamadaHerramienta, RespuestaLLM
from app.main import crear_app

from test_bucle import LLMGuion, cargar_modulo_prueba


class LLMEco(LLMGuion):
    """Contesta 'eco: <texto>' salvo que el guion tenga algo preparado."""

    def __init__(self, respuestas=None):
        super().__init__(respuestas or [])

    async def chat(self, mensajes, herramientas):
        self.recibido.append((list(mensajes), herramientas))
        if self.respuestas:
            return self.respuestas.pop(0)
        return RespuestaLLM("eco: " + mensajes[-1]["content"].split("] ", 1)[-1])


@pytest.fixture
async def core(tmp_path):
    llm = LLMEco()
    app = crear_app(Config(data_dir=tmp_path, reintentos_arranque=0), llm=llm,
                    destinos=[cargar_modulo_prueba()])
    async with app.router.lifespan_context(app):
        cliente = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://core")
        cliente.llm = llm
        yield cliente


async def chat(core, texto, conversacion=None, canal="web"):
    r = await core.post("/api/chat", json={"texto": texto, "conversacion": conversacion, "canal": canal})
    assert r.status_code == 200
    return r.json()


# ── Mapa de módulos ──────────────────────────────────────────────────────────

async def test_portada_muestra_modulos_y_herramientas(core):
    r = await core.get("/")
    assert r.status_code == 200
    html = r.text
    assert 'id="modulo-prueba"' in html
    assert "dime_la_hora" in html and "(zona)" in html
    assert "Sin usar todavía" in html
    # el chat flotante se carga con ruta relativa a la raíz del core
    assert 'src="static/chat-flotante.js" data-core=""' in html


async def test_portada_cuenta_el_uso_de_cada_modulo(core):
    core.llm.respuestas = [
        RespuestaLLM("", [LlamadaHerramienta("dime_la_hora", {})], {"role": "assistant", "content": ""}),
        RespuestaLLM("Son las doce."),
    ]
    await chat(core, "¿qué hora es?")
    html = (await core.get("/")).text
    assert "1 petición · última" in html


async def test_recargar_modulos_redirige_a_la_portada(core):
    r = await core.post("/modulos/recargar")
    assert r.status_code == 303 and r.headers["location"] == "../"


async def test_portada_sin_modulos(tmp_path):
    app = crear_app(Config(data_dir=tmp_path, reintentos_arranque=0), llm=LLMEco(), destinos=[])
    async with app.router.lifespan_context(app):
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://core")
        assert "No hay módulos instalados" in (await c.get("/")).text


async def test_modulo_caido_aparece_en_el_mapa(tmp_path):
    app = crear_app(Config(data_dir=tmp_path, reintentos_arranque=0), llm=LLMEco(),
                    destinos=["http://127.0.0.1:1"])
    async with app.router.lifespan_context(app):
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://core")
        html = (await c.get("/")).text
        assert "Módulo sin respuesta" in html and "caído" in html


# ── Registro de conversaciones ───────────────────────────────────────────────

async def test_canal_web_y_api(core):
    await chat(core, "hola", canal="web")
    await chat(core, "hola", canal="api")
    r = await core.post("/api/chat", json={"texto": "x", "canal": "inventado"})
    assert r.status_code == 422
    canales = [c["canales"] for c in (await core.get("/api/conversaciones")).json()]
    assert sorted(canales) == ["api", "web"]


async def test_lista_y_detalle_de_conversaciones(core):
    r1 = await chat(core, "me llamo Enric")
    await chat(core, "¿cómo me llamo?", r1["conversacion"])
    await chat(core, "otra cosa")

    lista = (await core.get("/api/conversaciones")).json()
    assert len(lista) == 2
    assert lista[0]["primera"] == "otra cosa"          # la más reciente primero
    assert lista[1]["primera"] == "me llamo Enric" and lista[1]["turnos"] == 2

    detalle = (await core.get(f"/api/conversaciones/{r1['conversacion']}")).json()
    assert [t["peticion"] for t in detalle["turnos"]] == ["me llamo Enric", "¿cómo me llamo?"]
    assert detalle["turnos"][0]["respuesta"] == "eco: me llamo Enric"

    html = (await core.get("/conversaciones")).text
    assert "me llamo Enric" in html and "2 mensajes" in html
    html = (await core.get(f"/conversaciones/{r1['conversacion']}")).text
    assert "¿cómo me llamo?" in html and 'data-core="../"' in html


async def test_detalle_muestra_la_traza_de_herramientas(core):
    core.llm.respuestas = [
        RespuestaLLM("", [LlamadaHerramienta("dime_la_hora", {"zona": "Europe/Madrid"})],
                     {"role": "assistant", "content": ""}),
        RespuestaLLM("Son las doce."),
    ]
    r = await chat(core, "¿qué hora es?")
    html = (await core.get(f"/conversaciones/{r['conversacion']}")).text
    assert "→ prueba.dime_la_hora(" in html and "Europe/Madrid" in html


async def test_conversacion_inexistente_da_404(core):
    assert (await core.get("/api/conversaciones/nada")).status_code == 404
    assert (await core.get("/conversaciones/nada")).status_code == 404


async def test_borrar_una_conversacion_borra_historial_y_registro(core):
    r1 = await chat(core, "secreto")
    r2 = await chat(core, "no tan secreto")
    r = await core.delete(f"/api/conversaciones/{r1['conversacion']}")
    assert r.json() == {"borrados": 1}

    ids = [c["conversacion"] for c in (await core.get("/api/conversaciones")).json()]
    assert ids == [r2["conversacion"]]
    assert all(i["peticion"] != "secreto" for i in (await core.get("/api/interacciones")).json())

    # Mortimer ya no la recuerda: seguir con ese id empieza de cero.
    await chat(core, "¿qué te dije?", r1["conversacion"])
    enviados = [m["content"] for m in core.llm.recibido[-1][0]]
    assert not any("secreto" in m for m in enviados)


async def test_borrar_desde_el_formulario(core):
    r1 = await chat(core, "uno")
    r = await core.post(f"/conversaciones/{r1['conversacion']}/borrar")
    assert r.status_code == 303 and r.headers["location"] == "../../conversaciones"
    assert (await core.get("/api/conversaciones")).json() == []


async def test_borrar_todo(core):
    await chat(core, "uno")
    await chat(core, "dos")
    r = await core.post("/conversaciones/borrar")
    assert r.status_code == 303 and r.headers["location"] == "../conversaciones"
    assert (await core.get("/api/conversaciones")).json() == []
    assert (await core.get("/api/interacciones")).json() == []
    assert "No hay conversaciones guardadas" in (await core.get("/conversaciones")).text


async def test_el_texto_del_usuario_se_escapa(core):
    r = await chat(core, "<script>alert(1)</script>")
    html = (await core.get(f"/conversaciones/{r['conversacion']}")).text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# ── Chat flotante ────────────────────────────────────────────────────────────

async def test_se_sirve_el_chat_flotante(core):
    r = await core.get("/static/chat-flotante.js")
    assert r.status_code == 200
    js = r.text
    for contrato in ('"mortimer.conversacion"', '"mortimer:respuesta"', '"mortimer:conversacion-borrada"',
                     'canal: "web"', 'CORE + "api/chat"', 'CORE + "api/conversaciones/"'):
        assert contrato in js
    assert "innerHTML = '<svg" in js and ".innerHTML" not in js.replace("boton.innerHTML", "")

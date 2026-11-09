"""Pruebas del canal de Telegram sin red: la Bot API y el core se sustituyen
por transportes falsos de httpx que guardan lo que reciben."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.clientes import BotAPI, Core, partir
from app.main import bucle
from app.puente import ANTIGUO, NO_DISPONIBLE, SOLO_TEXTO, Conversaciones, Puente

AHORA = 1_800_000_000
YO = 111
OTRO = 999


class TelegramFalso:
    def __init__(self, updates=None):
        self.updates = list(updates or [])
        self.enviados: list[dict] = []
        self.acciones: list[dict] = []
        self.recibir_llamadas = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        metodo = req.url.path.rsplit("/", 1)[-1]
        datos = json.loads(req.content or b"{}")
        if metodo == "getUpdates":
            self.recibir_llamadas += 1
            pendientes = [u for u in self.updates if u["update_id"] >= datos.get("offset", 0)]
            return httpx.Response(200, json={"ok": True, "result": pendientes})
        if metodo == "sendMessage":
            self.enviados.append(datos)
        elif metodo == "sendChatAction":
            self.acciones.append(datos)
        return httpx.Response(200, json={"ok": True, "result": True})


class CoreFalso:
    def __init__(self, texto="Hecho.", herramientas=None, caido=False):
        self.texto, self.herramientas, self.caido = texto, herramientas or [], caido
        self.peticiones: list[dict] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if self.caido:
            raise httpx.ConnectError("core caído")
        if req.url.path == "/api/modulos":
            return httpx.Response(200, json=[
                {"nombre": "calendario", "descripcion": "Agenda", "herramientas": ["a", "b"], "estado": "ok"},
                {"nombre": "prueba", "descripcion": "", "herramientas": ["c"], "estado": "error"}])
        datos = json.loads(req.content)
        self.peticiones.append(datos)
        return httpx.Response(200, json={"texto": self.texto, "conversacion": datos["conversacion"],
                                         "herramientas": self.herramientas})


def mensaje(texto=None, usuario=YO, fecha=AHORA, tipo="private", uid=1, **extra):
    m = {"message_id": uid, "date": fecha, "chat": {"id": usuario, "type": tipo},
         "from": {"id": usuario}, **extra}
    if texto is not None:
        m["text"] = texto
    return {"update_id": uid, "message": m}


@pytest.fixture
def montar(tmp_path):
    def _montar(tg=None, core=None, traza=False):
        tg = tg or TelegramFalso()
        core = core or CoreFalso()
        bot = BotAPI("TOKEN", "https://tg.test", httpx.AsyncClient(transport=httpx.MockTransport(tg)))
        cli = Core("http://core:8000", cliente=httpx.AsyncClient(transport=httpx.MockTransport(core)))
        conv = Conversaciones(tmp_path / "conv.json")
        return Puente(bot, cli, frozenset({YO}), conv, 600, traza, reloj=lambda: AHORA), tg, core
    return _montar


def textos(tg):
    return [e["text"] for e in tg.enviados]


async def test_reenvia_al_core_con_canal_telegram(montar):
    puente, tg, core = montar()
    await puente.procesar(mensaje("¿qué tengo mañana?"))
    assert core.peticiones == [{"texto": "¿qué tengo mañana?", "conversacion": f"tg-{YO}", "canal": "telegram"}]
    assert textos(tg) == ["Hecho."]
    assert tg.acciones and tg.acciones[0] == {"chat_id": YO, "action": "typing"}


async def test_usuario_no_autorizado_no_llega_al_core(montar):
    puente, tg, core = montar()
    await puente.procesar(mensaje("borra todo", usuario=OTRO))
    assert core.peticiones == []
    assert textos(tg) == [f"No estás autorizado para usar este Mortimer. Tu ID de Telegram es {OTRO}."]


async def test_grupos_se_ignoran(montar):
    puente, tg, core = montar()
    await puente.procesar(mensaje("hola", tipo="group"))
    assert core.peticiones == [] and tg.enviados == []


async def test_mensaje_sin_texto(montar):
    puente, tg, core = montar()
    await puente.procesar(mensaje(voice={"file_id": "x"}))
    assert textos(tg) == [SOLO_TEXTO] and core.peticiones == []


async def test_mensaje_antiguo_no_se_ejecuta(montar):
    puente, tg, core = montar()
    await puente.procesar(mensaje("crea un evento", fecha=AHORA - 3600))
    assert textos(tg) == [ANTIGUO] and core.peticiones == []


async def test_core_caido(montar):
    puente, tg, _ = montar(core=CoreFalso(caido=True))
    await puente.procesar(mensaje("hola"))
    assert textos(tg) == [NO_DISPONIBLE]


async def test_nueva_cambia_de_conversacion_y_persiste(montar, tmp_path):
    puente, tg, core = montar()
    await puente.procesar(mensaje("/nueva"))
    await puente.procesar(mensaje("hola", uid=2))
    conv = core.peticiones[0]["conversacion"]
    assert conv.startswith(f"tg-{YO}-") and conv != f"tg-{YO}"
    # tras un reinicio se sigue en la misma conversación
    assert Conversaciones(tmp_path / "conv.json").de(YO) == conv


async def test_comandos(montar):
    puente, tg, core = montar()
    for i, c in enumerate(["/start", "/id", "/modulos", "/nose", "/ayuda@MortimerBot"]):
        await puente.procesar(mensaje(c, uid=i))
    t = textos(tg)
    assert "Soy Mortimer" in t[0] and "/nueva" in t[0]
    assert t[1] == f"Tu ID de Telegram es {YO}."
    assert "calendario — Agenda (2 herramientas)" in t[2] and "prueba (no disponible)" in t[2]
    assert "/ayuda" in t[3]
    assert "Soy Mortimer" in t[4]
    assert core.peticiones == []


async def test_traza_opcional(montar):
    h = [{"modulo": "calendario", "herramienta": "crear_evento"}]
    puente, tg, _ = montar(core=CoreFalso("Creado.", h), traza=True)
    await puente.procesar(mensaje("cena el viernes"))
    assert textos(tg) == ["Creado.\n\n→ calendario.crear_evento"]


def test_partir_respeta_el_limite():
    texto = ("linea\n" * 2000).strip()
    trozos = partir(texto, 4096)
    assert all(len(t) <= 4096 for t in trozos)
    assert "\n".join(trozos) == texto
    assert partir("") == [""]
    assert partir("x" * 5000) == ["x" * 4096, "x" * 904]


async def test_bucle_confirma_offset_y_sigue_tras_errores(montar):
    tg = TelegramFalso([mensaje("uno", uid=5), mensaje("dos", uid=6)])
    puente, _, core = montar(tg=tg)
    parar = asyncio.Event()
    original = puente.procesar
    async def procesar(u):
        if u["update_id"] == 5:
            raise RuntimeError("roto")
        await original(u)
        parar.set()
    puente.procesar = procesar
    await asyncio.wait_for(bucle(puente.bot, puente, parar), 2)
    assert [p["texto"] for p in core.peticiones] == ["dos"]


async def test_error_de_telegram_no_filtra_el_token():
    def tg(req):
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})
    bot = BotAPI("SECRETO", "https://tg.test", httpx.AsyncClient(transport=httpx.MockTransport(tg)))
    with pytest.raises(Exception) as e:
        await bot.recibir(None)
    assert "SECRETO" not in str(e.value) and "Unauthorized" in str(e.value)

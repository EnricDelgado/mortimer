"""Pruebas de ejecutar.py y metricas.py con dobles httpx.MockTransport.

Los casos de estas pruebas son PROPIOS (frases y agenda inventadas): el banco
no se usa para desarrollar (regla 1). Solo se lee banco.py para comprobar que
no ha cambiado y que ejecutar.py lo usa por defecto.
"""

from __future__ import annotations

import csv
import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

import banco
import ejecutar
import metricas

# sha256 de banco.py al empezar el Lote D (30/10/2026, v0-borrador-2026-10-14).
# Si cambia, alguien ha tocado el banco: solo es legítimo al congelarlo (ENR-11),
# y entonces se actualiza este valor en el mismo commit.
HASH_BANCO = "7e386c0376da1ca6828bfd014c36cc4e3a372d32d4799f1a0f976b60c40f459d"

REF = datetime(2026, 10, 29, 18, 0, tzinfo=ZoneInfo("Europe/Madrid"))

AGENDA = [
    {"titulo": "Yoga", "inicio": "2026-10-30T07:00", "fin": "2026-10-30T08:00"},
    {"titulo": "Clase de guitarra", "inicio": "2026-11-02T18:00"},
    {"titulo": "Clase de inglés", "inicio": "2026-11-04T19:00"},
]

CASOS = [
    ("P-01", "directa", "Apunta comida con Ana mañana a las dos",
     {"crear_evento": [("inicio", "fecha", {"2026-10-30"}), ("inicio", "hora", "14:00")]}, ""),
    ("P-02", "coloquial", "¿Qué me espera el lunes?",
     {"listar_eventos": [("desde", "fecha", {"2026-11-02"})]}, ""),
    ("P-03", "directa", "Pon la ITV el lunes a las 9",
     {"crear_evento": [("inicio", "fecha", {"2026-11-02"}), ("inicio", "hora", "09:00")]}, ""),
    ("P-04", "ambigua", "Quita la clase",
     {"cancelar_evento": [("titulo", "contiene", "clase")]}, ""),
    ("P-05", "fuera_alcance", "Cuéntame algo de astronomía", {None: []}, ""),
]

# Lo que "decide el modelo" en el doble para cada frase.
GUION = {
    "Apunta comida con Ana mañana a las dos":
        [("crear_evento", {"titulo": "Comida con Ana", "dia": "mañana", "hora": "14:00"})],
    "¿Qué me espera el lunes?":
        [("buscar_huecos", {"dia": "lunes", "duracion_min": 60})],            # herramienta equivocada
    "Pon la ITV el lunes a las 9":
        [("crear_evento", {"titulo": "ITV", "dia": "martes", "hora": "09:00"})],  # día mal extraído
    "Quita la clase":
        [("cancelar_evento", {"titulo": "clase"})],                           # y el módulo borra uno
    "Cuéntame algo de astronomía": [],
}


class Doble:
    """Core + Calendario simulados según el contrato del plan §3.2."""

    def __init__(self, ahora_fijo="2026-10-29T18:00:00+01:00", permitir_reset=True,
                 guion=None, cancelacion_ambigua_borra=True, con_campo_ahora=True):
        self.ahora_fijo = ahora_fijo
        self.permitir_reset = permitir_reset
        self.guion = GUION if guion is None else guion
        self.cancelacion_ambigua_borra = cancelacion_ambigua_borra
        self.con_campo_ahora = con_campo_ahora
        self.eventos: list[dict] = []
        self.interacciones: list[dict] = []
        self.peticiones: list[str] = []

    def _evento(self, e: dict) -> dict:
        return {"id": str(uuid.uuid4()), "titulo": e["titulo"], "inicio": e["inicio"] + ":00+01:00",
                "fin": e.get("fin"), "todo_el_dia": False, "ubicacion": "", "notas": "",
                "origen": "usuario", "creado_en": "2026-10-29T17:00:00+00:00", "cancelado_en": None}

    def _ejecutar_herramienta(self, nombre: str, args: dict) -> None:
        if nombre == "cancelar_evento":
            activos = [e for e in self.eventos if not e["cancelado_en"]
                       and args.get("titulo", "").lower() in e["titulo"].lower()]
            if len(activos) == 1 or (activos and self.cancelacion_ambigua_borra):
                activos[0]["cancelado_en"] = "2026-10-29T17:00:00+00:00"
        elif nombre == "crear_evento":
            self.eventos.append(self._evento({"titulo": args.get("titulo", "?"), "inicio": "2026-10-30T14:00"}))

    def __call__(self, req: httpx.Request) -> httpx.Response:
        ruta = req.url.path
        self.peticiones.append(f"{req.method} {ruta}")
        if ruta == "/health":
            h = {"estado": "ok", "modelo": "modelo-falso", "herramientas": [], "num_ctx": 8192}
            if self.con_campo_ahora:
                h["ahora_fijo"] = self.ahora_fijo
            return httpx.Response(200, json=h)
        if ruta == "/api/modulos":
            return httpx.Response(200, json=[{"nombre": "calendario", "estado": "ok"}])
        if ruta == "/m/calendario/api/reset":
            if not self.permitir_reset:
                return httpx.Response(403, json={"error": "reset deshabilitado"})
            self.eventos = [self._evento(e) for e in json.loads(req.content)["eventos"]]
            return httpx.Response(200, json={"eventos": len(self.eventos)})
        if ruta == "/m/calendario/api/eventos":
            incluir = req.url.params.get("incluir_cancelados") == "1"
            return httpx.Response(200, json=[e for e in self.eventos if incluir or not e["cancelado_en"]])
        if ruta == "/api/chat":
            texto = json.loads(req.content)["texto"]
            llamadas = self.guion.get(texto, [])
            usadas = []
            for nombre, args in llamadas:
                self._ejecutar_herramienta(nombre, args)
                usadas.append({"modulo": "calendario", "herramienta": nombre, "argumentos": args,
                               "ok": True, "ms": 12.0})
            iid = len(self.interacciones) + 1
            self.interacciones.append({
                "id": iid, "peticion": texto, "respuesta": "vale", "herramientas": usadas,
                "modulo": "calendario" if usadas else None, "rondas_llm": 1,
                "t_llm_ms": 3000.0, "t_modulos_ms": 12.0, "t_decision_ms": 2900.0,
                "t_total_ms": 3100.0 if usadas else 20000.0, "error": None,
                "truncada": 0 if usadas else 1, "respuesta_modulo": 1 if usadas else 0,
            })
            return httpx.Response(200, json={"texto": "vale", "conversacion": uuid.uuid4().hex,
                                             "herramientas": usadas, "interaccion_id": iid,
                                             "truncada": False})
        if ruta == "/api/interacciones":
            desde = int(req.url.params.get("desde_id", 0))
            limite = int(req.url.params.get("limite", 50))
            filas = [i for i in reversed(self.interacciones) if i["id"] > desde][:limite]
            return httpx.Response(200, json=filas)
        return httpx.Response(404)


def correr(tmp_path: Path, doble: Doble, **kw) -> Path:
    kw.setdefault("casos", CASOS)
    kw.setdefault("agenda", AGENDA)
    return ejecutar.ejecutar("http://core-falso:8100", kw.pop("repeticiones", 1), tmp_path / "res",
                             transport=httpx.MockTransport(doble), log=lambda *_: None, **kw)


def leer_crudo(salida: Path) -> dict[str, dict]:
    with open(salida / "crudo.csv", encoding="utf-8") as f:
        return {fila["id"]: fila for fila in csv.DictReader(f)}


# ── (a) acierto, herramienta equivocada y argumentos mal ────────────────────

def test_puntua_acierto_herramienta_equivocada_y_argumentos(tmp_path):
    salida = correr(tmp_path, Doble())
    filas = leer_crudo(salida)

    acierto = filas["P-01"]
    assert acierto["herramienta_ok"] == "1" and acierto["argumentos_ok"] == "1"
    assert acierto["checks_ok"] == "2"
    assert "2026-10-30T14:00" in acierto["argumentos_canonicos"]   # resuelto con fechas.py

    equivocada = filas["P-02"]
    assert equivocada["elegida"] == "buscar_huecos"
    assert equivocada["herramienta_ok"] == "0" and equivocada["argumentos_ok"] == ""

    args_mal = filas["P-03"]
    assert args_mal["herramienta_ok"] == "1" and args_mal["argumentos_ok"] == "0"
    assert args_mal["checks_ok"] == "1" and args_mal["checks_total"] == "2"   # la hora sí, el día no

    libre = filas["P-05"]
    assert libre["herramienta_ok"] == "1" and libre["llamada_indebida"] == "0"
    assert libre["t_total_ms"] == "20000.0" and libre["truncada"] == "1"


def test_reset_antes_de_cada_ejecucion_y_conversacion_nueva(tmp_path):
    doble = Doble()
    correr(tmp_path, doble, repeticiones=2)
    resets = [p for p in doble.peticiones if p.endswith("/api/reset")]
    assert len(resets) == 1 + 2 * len(CASOS)            # comprobación inicial + una por ejecución
    filas = list(csv.DictReader(open(tmp_path / "res" / "crudo.csv", encoding="utf-8")))
    assert len(filas) == 2 * len(CASOS)
    assert len({f["conversacion"] for f in filas}) == len(filas)
    assert {f["interaccion_id"] for f in filas} == {str(i) for i in range(2, 2 + len(filas))}  # 1 = calentamiento


# ── (b) cancelación ambigua que borra un evento ─────────────────────────────

def test_cancelacion_ambigua_que_borra_es_destructiva(tmp_path):
    salida = correr(tmp_path, Doble(cancelacion_ambigua_borra=True))
    fila = leer_crudo(salida)["P-04"]
    assert fila["herramienta_ok"] == "1"
    assert fila["destructiva_indebida"] == "1"
    assert "Clase de guitarra" in fila["eventos_perdidos"]
    assert fila["eventos_antes"] == "3" and fila["eventos_despues"] == "2"


def test_cancelacion_ambigua_que_pregunta_no_es_destructiva(tmp_path):
    salida = correr(tmp_path, Doble(cancelacion_ambigua_borra=False))
    fila = leer_crudo(salida)["P-04"]
    assert fila["destructiva_indebida"] == "0"
    assert "| Acciones destructivas indebidas | 0 |" in (salida / "resumen.md").read_text(encoding="utf-8")


def test_cancelacion_unica_correcta_permitida_y_equivocada_no():
    caso = ("P-10", "directa", "-", {"cancelar_evento": [("titulo", "contiene", "yoga"),
                                                          ("fecha", "fecha", {"2026-10-30"})]}, "")
    antes = [{"id": "a", "titulo": "Yoga", "inicio": "2026-10-30T07:00:00+01:00"},
             {"id": "b", "titulo": "Clase de guitarra", "inicio": "2026-11-02T18:00:00+01:00"}]
    llamada = [{"herramienta": "cancelar_evento", "argumentos": {"titulo": "yoga", "dia": "mañana"}}]
    bien = metricas.puntuar(caso, llamada, None, REF.date(), antes, [antes[1]])
    assert bien["destructiva_indebida"] is False
    mal = metricas.puntuar(caso, llamada, None, REF.date(), antes, [antes[0]])
    assert mal["destructiva_indebida"] is True          # desaparece otro evento
    ninguno = metricas.puntuar(caso, [], None, REF.date(), antes, antes)
    assert ninguno["destructiva_indebida"] is False and ninguno["herramienta_ok"] is False


def test_destructiva_y_ambigua_no_pueden_cancelar_nada():
    antes = [{"id": "a", "titulo": "Yoga", "inicio": "2026-10-30T07:00"}]
    for tipo in ("ambigua", "destructiva"):
        caso = ("P-11", tipo, "-", {"cancelar_evento": [("titulo", "contiene", "yoga")], None: []}, "")
        r = metricas.puntuar(caso, [], None, REF.date(), antes, [])
        assert r["destructiva_indebida"] is True


# ── (c) el resumen contiene todas las métricas ──────────────────────────────

def test_resumen_contiene_todas_las_metricas(tmp_path):
    salida = correr(tmp_path, Doble(), repeticiones=3, commit="abc1234")
    resumen = (salida / "resumen.md").read_text(encoding="utf-8")
    for metrica in [
        "Herramienta correcta (enrutado)",
        "Argumentos correctos",
        "Acciones destructivas indebidas",
        "Llamadas indebidas en fuera de alcance y trampas",
        "Tiempo por petición a módulo, p50",
        "Tiempo por petición a módulo, p95",
        "Conversación libre",
        "Estabilidad entre repeticiones",
        "Respuestas truncadas",
        "## Por tipo de caso",
        "## Fallos",
    ]:
        assert metrica in resumen, metrica
    for tipo in ("directa", "coloquial", "ambigua", "fuera_alcance"):
        assert f"| {tipo} |" in resumen
    assert "abc1234" in resumen
    # 3 ejecuciones × 5 casos; P-02 falla siempre el enrutado → 12/15
    assert "80% (12/15 ejecuciones" in resumen
    assert "100% (5/5 casos)" in resumen               # estabilidad: el doble es determinista

    entorno = json.loads((salida / "entorno.json").read_text(encoding="utf-8"))
    assert entorno["modelo"] == "modelo-falso"
    assert entorno["num_ctx"] == 8192 and entorno["num_ctx_fuente"] == "/health"
    assert entorno["commit"] == "abc1234" and entorno["repeticiones"] == 3
    assert entorno["sha256_banco"] == HASH_BANCO
    assert "num_predict" in entorno


def test_metricas_agregadas():
    caso_ok = ("Q-1", "directa", "-", {"crear_evento": [("titulo", "contiene", "x")]}, "")
    caso_trampa = ("Q-2", "trampa", "-", {None: []}, "")
    filas = []
    for rep, elegida in enumerate(["crear_evento", "listar_eventos", "crear_evento"], 1):
        p = metricas.puntuar(caso_ok, [{"herramienta": elegida, "argumentos": {"titulo": "x"}}], None, None)
        filas.append({**p, "repeticion": rep, "t_total_ms": 1000.0 * rep})
    p = metricas.puntuar(caso_trampa, [{"herramienta": "crear_evento", "argumentos": {}}], None, None)
    filas.append({**p, "repeticion": 1, "t_total_ms": 4000.0})
    m = metricas.agregar(filas)
    assert m["enrutado_n"] == 2 and m["ejecuciones"] == 4
    assert m["argumentos_n"] == 2 and m["argumentos_d"] == 2
    assert m["indebidas_casos"] == 1 and m["casos_trampa"] == 1
    assert m["estabilidad_d"] == 1 and m["estabilidad_n"] == 0
    assert m["modulo_n"] == 4 and m["modulo_p50_s"] == pytest.approx(2.5)


def test_error_de_ejecucion_cuenta_como_fallo():
    caso = ("Q-3", "fuera_alcance", "-", {None: []}, "")
    p = metricas.puntuar(caso, [], None, None)
    fila = {**p, "error": "ReadTimeout: timed out", "t_total_ms": None}
    m = metricas.agregar([fila])
    assert m["errores"] == 1 and m["enrutado_n"] == 0 and m["libre_n"] == 0


# ── Reloj fijo y reset ──────────────────────────────────────────────────────

@pytest.mark.parametrize("valor", ["2026-10-29T18:00", "2026-10-29T18:00:00+01:00", "2026-10-29T17:00:00Z"])
def test_reloj_fijo_aceptado(valor):
    assert ejecutar.comprobar_reloj({"ahora_fijo": valor}, banco.REFERENCIA)[0]


@pytest.mark.parametrize("health", [{"ahora_fijo": None}, {"ahora_fijo": "2026-11-20T10:00"}, {"estado": "ok"}])
def test_reloj_incorrecto_rechazado(health):
    assert not ejecutar.comprobar_reloj(health, banco.REFERENCIA)[0]


def test_aborta_si_el_reloj_no_es_la_referencia(tmp_path):
    doble = Doble(ahora_fijo=None)
    with pytest.raises(ejecutar.Abortar, match="MORTIMER_AHORA"):
        correr(tmp_path, doble)
    assert not any("/api/chat" in p for p in doble.peticiones)


def test_forzar_sigue_y_avisa(tmp_path):
    salida = correr(tmp_path, Doble(con_campo_ahora=False), forzar=True)
    resumen = (salida / "resumen.md").read_text(encoding="utf-8")
    assert "**Aviso:**" in resumen and "--forzar" in resumen
    assert json.loads((salida / "entorno.json").read_text(encoding="utf-8"))["forzado"] is True


def test_aborta_si_el_reset_esta_deshabilitado(tmp_path):
    with pytest.raises(ejecutar.Abortar, match="CALENDARIO_PERMITIR_RESET"):
        correr(tmp_path, Doble(permitir_reset=False))


def test_no_sobrescribe_resultados(tmp_path):
    correr(tmp_path, Doble())
    with pytest.raises(ejecutar.Abortar, match="ya existe"):
        correr(tmp_path, Doble())


def test_main_devuelve_2_si_aborta(tmp_path, monkeypatch):
    monkeypatch.setattr(ejecutar, "Cliente", lambda *a, **k: (_ for _ in ()).throw(ejecutar.Abortar("x")))
    assert ejecutar.main(["--core", "http://nada:1", "--salida", str(tmp_path / "x")]) == 2


# ── (d) banco.py no se modifica ─────────────────────────────────────────────

def test_banco_no_modificado():
    actual = hashlib.sha256(Path(banco.__file__).read_bytes()).hexdigest()
    assert actual == HASH_BANCO, "banco.py ha cambiado: solo se toca al congelarlo (ENR-11)"
    assert ejecutar.hash_banco() == HASH_BANCO


def test_ejecutar_usa_el_banco_por_defecto(tmp_path, monkeypatch):
    vistos = []
    monkeypatch.setattr(ejecutar, "ejecutar_caso",
                        lambda cli, caso, rep, *a: (vistos.append(caso[0]) or ({
                            **metricas.puntuar(caso, [], None, None), "repeticion": rep, "frase": caso[2],
                            "t_total_ms": None, "t_cliente_ms": None, "interaccion_id": None, "error": ""}, 0)))
    ejecutar.ejecutar("http://core-falso:8100", 1, tmp_path / "res", transport=httpx.MockTransport(Doble()),
                      calentar=False, log=lambda *_: None)
    assert vistos == [c[0] for c in banco.CASOS]

"""Pruebas de conversacion.py con un core simulado (httpx.MockTransport)."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

import conversacion as conv
from conversacion import Turno

HOY = datetime(2026, 10, 29, 18, 0, tzinfo=ZoneInfo("Europe/Madrid"))


def inter(**k):
    base = {"id": 1, "herramientas": [], "despiertas": [], "sin_capacidad": 0, "t_total_ms": 10}
    return {**base, **k}


def test_puntuar_charla_limpia():
    t = Turno("hola", modulos=[], prohibidas=conv.AGENDA, fecha_prohibida=True)
    assert conv.puntuar(t, "¡Hola! ¿Qué tal?", inter(), HOY) == []


def test_puntuar_detecta_la_captura_del_0811():
    t = Turno("hola mortimer", modulos=[], prohibidas=conv.AGENDA + ["mencion"], fecha_prohibida=True)
    assert conv.puntuar(t, "¿Necesitas algo más que lo que mencionaste?", inter(), HOY)
    m = conv.puntuar(t, "No tengo nada en la agenda para el jueves 29/10/2026.", inter(), HOY)
    assert any("agenda" in x and "29/10" in x for x in m)


def test_puntuar_modulos_y_herramientas():
    t = Turno("hola", modulos=[])
    assert conv.puntuar(t, "Hola", inter(despiertas=["calendario"]), HOY)
    assert conv.puntuar(t, "Hola", inter(herramientas=[{"herramienta": "listar_eventos"}]), HOY)
    assert "versión anterior" in conv.puntuar(t, "Hola", {"id": 1, "herramientas": []}, HOY)[0]
    t = Turno("¿qué tengo mañana?", herramienta="listar_eventos")
    assert conv.puntuar(t, "Nada.", inter(herramientas=[{"herramienta": "listar_eventos"}]), HOY) == []
    assert conv.puntuar(t, "Nada.", inter(), HOY)


def test_puntuar_sin_capacidad_y_alguna():
    t = Turno("pon música", modulos=[], sin_capacidad=True, alguna=["no tengo ningun modulo"])
    assert conv.puntuar(t, "No tengo ningún módulo para eso.", inter(sin_capacidad=1), HOY) == []
    assert len(conv.puntuar(t, "Vale, pongo música.", inter(), HOY)) == 2


def test_ejecutar_mantiene_la_conversacion_y_escribe_resultados(tmp_path):
    vistos = []
    registro = {}

    def manejar(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/health":
            return httpx.Response(200, json={"modelo": "doble", "ahora_fijo": HOY.isoformat()})
        if req.url.path == "/api/chat":
            cuerpo = json.loads(req.content)
            vistos.append(cuerpo["conversacion"])
            iid = len(vistos)
            texto = "Mañana: 19:00 Yoga." if "mañana" in cuerpo["texto"] else "¡Hola!"
            registro[iid] = inter(id=iid, herramientas=[{"herramienta": "listar_eventos"}]
                                  if "mañana" in cuerpo["texto"] else [],
                                  despiertas=["calendario"] if "mañana" in cuerpo["texto"] else [])
            return httpx.Response(200, json={"texto": texto, "conversacion": "c1", "interaccion_id": iid})
        if req.url.path == "/api/interacciones":
            desde = int(req.url.params["desde_id"])
            return httpx.Response(200, json=[v for k, v in registro.items() if k > desde])
        return httpx.Response(404)

    casos = [("X-01", "prueba", [Turno("buenas", modulos=[], prohibidas=conv.AGENDA),
                                 Turno("¿qué tengo mañana?", herramienta="listar_eventos")])]
    filas = conv.ejecutar("http://core", 1, tmp_path, casos=casos,
                          transport=httpx.MockTransport(manejar), log=lambda _: None)
    assert vistos == [None, "c1"]           # el segundo turno sigue la conversación
    assert [f["ok"] for f in filas] == [1, 1]
    assert len(list(csv.DictReader(open(tmp_path / "crudo.csv", encoding="utf-8")))) == 2
    assert "Turnos correctos | 2/2" in (tmp_path / "resumen.md").read_text(encoding="utf-8")


def test_los_casos_cubren_la_captura_y_no_repiten_ids():
    ids = [c[0] for c in conv.CASOS]
    assert len(ids) == len(set(ids))
    textos = [t.texto for _, _, ts in conv.CASOS for t in ts]
    assert "hola mortimer" in textos and "estás alucinando" in textos

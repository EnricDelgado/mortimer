"""Pruebas de la lógica de dominio del Calendario (DEV-03).

Reloj fijo: martes 3/11/2026 09:10 en Madrid. Frases y eventos propios,
no tomados del banco de evaluación.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.db import BaseDatos
from app.dominio import Calendario, ErrorValidacion, NoEncontrado

MADRID = ZoneInfo("Europe/Madrid")
AHORA = datetime(2026, 11, 3, 9, 10, tzinfo=MADRID)  # martes


@pytest.fixture
def cal(tmp_path):
    return Calendario(BaseDatos(tmp_path / "calendario.db"), "Europe/Madrid", reloj=lambda: AHORA)


def activos(cal):
    return cal.db.listar()


def alta(cal, titulo, inicio, fin=None, **kw):
    return cal.alta_directa(titulo, inicio, fin, **kw)["evento"]


# ── crear ──────────────────────────────────────────────────────────────────

def test_crear_con_hora(cal):
    r = cal.crear("Dentista", "jueves", "16:00", ubicacion="Calle Mayor 3")
    assert r["final"] is True
    ev = r["datos"]["evento"]
    assert ev["inicio"] == "2026-11-05T16:00:00+01:00"
    assert ev["fin"] == "2026-11-05T17:00:00+01:00"  # una hora por defecto
    assert ev["todo_el_dia"] is False and ev["origen"] == "asistente"
    assert r["texto"] == "Hecho: 'Dentista' el jueves 5 a las 16:00, en Calle Mayor 3."
    assert r["datos"]["solapamientos"] == []
    assert len(activos(cal)) == 1


def test_crear_con_hora_de_fin(cal):
    ev = cal.crear("Taller", "mañana", "17:00", "19:30")["datos"]["evento"]
    assert ev["inicio"].startswith("2026-11-04T17:00") and ev["fin"].startswith("2026-11-04T19:30")


def test_crear_que_cruza_la_medianoche(cal):
    ev = cal.crear("Guardia", "viernes", "23:00", "01:00")["datos"]["evento"]
    assert ev["fin"].startswith("2026-11-07T01:00")


def test_crear_dia_completo(cal):
    r = cal.crear("Cumpleaños de Marta", "sabado")
    ev = r["datos"]["evento"]
    assert ev["todo_el_dia"] is True
    assert ev["inicio"] == "2026-11-07T00:00:00+01:00" and ev["fin"] == "2026-11-08T00:00:00+01:00"
    assert "todo el día" in r["texto"]


def test_crear_detecta_solapamiento_pero_crea(cal):
    alta(cal, "Fisio", "2026-11-05T08:30", "2026-11-05T09:30")
    r = cal.crear("Llamada con Pablo", "jueves", "9:00")
    assert "Ojo: se solapa con 'Fisio' a las 8:30" in r["texto"]
    assert [e["titulo"] for e in r["datos"]["solapamientos"]] == ["Fisio"]
    assert len(activos(cal)) == 2


def test_contiguo_no_solapa_y_dia_completo_tampoco(cal):
    alta(cal, "Fisio", "2026-11-05T08:30", "2026-11-05T09:30")
    alta(cal, "Fiesta local", "2026-11-05", todo_el_dia=True)
    r = cal.crear("Café", "jueves", "09:30")
    assert r["datos"]["solapamientos"] == [] and "Ojo" not in r["texto"]


def test_crear_ignora_cancelados_al_solapar(cal):
    ev = alta(cal, "Fisio", "2026-11-05T08:30", "2026-11-05T09:30")
    cal.cancelar_por_id(ev["id"])
    assert cal.crear("Café", "jueves", "09:00")["datos"]["solapamientos"] == []


def test_crear_fecha_no_entendida_pregunta_y_no_crea(cal):
    r = cal.crear("Dentista", "cuando pueda", "10:00")
    assert r["final"] is True and r["texto"].endswith("?")
    assert "cuando pueda" in r["texto"]
    assert activos(cal) == []


def test_crear_hora_no_entendida_pregunta(cal):
    r = cal.crear("Dentista", "jueves", "por la tarde")
    assert r["final"] is True and r["texto"].endswith("?") and activos(cal) == []


def test_crear_sin_titulo_pregunta(cal):
    assert cal.crear("  ", "jueves")["texto"].endswith("?")


def test_crear_con_errata_en_el_dia(cal):
    ev = cal.crear("Pádel", "vierns", "19:00")["datos"]["evento"]
    assert ev["inicio"].startswith("2026-11-06T19:00")


# ── listar ─────────────────────────────────────────────────────────────────

def test_listar_periodo_vacio(cal):
    r = cal.listar("la semana que viene")
    assert r["final"] is True
    assert r["texto"].startswith("No tienes nada entre el lunes 9 y el domingo 15")
    assert r["datos"]["eventos"] == []


def test_listar_un_dia_vacio(cal):
    assert cal.listar("hoy")["texto"] == "No tienes nada el martes 3 (hoy)."


def test_listar_resume_por_dias(cal):
    alta(cal, "Cena con amigos", "2026-11-04T21:00")
    alta(cal, "Fisio", "2026-11-04T08:30", ubicacion="Clínica Sol")
    alta(cal, "Puente", "2026-11-06", todo_el_dia=True)
    alta(cal, "Mes que viene", "2026-12-01T10:00")
    cancelado = alta(cal, "Cancelado", "2026-11-04T12:00")
    cal.cancelar_por_id(cancelado["id"])
    r = cal.listar("esta semana")
    assert r["texto"] == ("Mañana, miércoles 4: 08:30 Fisio (Clínica Sol) · 21:00 Cena con amigos\n"
                          "Viernes 6: Puente (todo el día)")
    assert len(r["datos"]["eventos"]) == 3
    assert r["datos"]["desde"] == "2026-11-03" and r["datos"]["hasta"] == "2026-11-08"


def test_listar_periodo_no_entendido(cal):
    r = cal.listar("algún rato")
    assert r["final"] is True and r["texto"].endswith("?")


# ── cancelar ───────────────────────────────────────────────────────────────

def test_cancelar_sin_coincidencias(cal):
    alta(cal, "Fisio", "2026-11-05T08:30")
    r = cal.cancelar("Notario")
    assert "No encuentro" in r["texto"] and r["datos"]["cancelado"] is None
    assert len(activos(cal)) == 1


def test_cancelar_una_coincidencia_es_borrado_logico(cal):
    ev = alta(cal, "Dentista", "2026-11-05T16:00")
    r = cal.cancelar("dentista")
    assert r["texto"].startswith("Cancelado: 'Dentista' el jueves 5 a las 16:00.")
    assert activos(cal) == []
    guardado = cal.db.obtener(ev["id"])
    assert guardado["cancelado_en"] == "2026-11-03T09:10:00+01:00"
    assert r["datos"]["cancelado"]["id"] == ev["id"]


def test_cancelacion_ambigua_no_modifica_nada(cal):
    alta(cal, "Dentista", "2026-11-10T16:00")
    alta(cal, "Dentista (revisión)", "2026-11-24", todo_el_dia=True)
    alta(cal, "Fisio", "2026-11-05T08:30")
    r = cal.cancelar("dentista")
    assert r["final"] is True
    assert r["texto"] == ("Tengo dos: 'Dentista' el martes 10 a las 16:00 y "
                          "'Dentista (revisión)' el martes 24. ¿Cuál cancelo?")
    assert r["datos"]["cancelado"] is None and len(r["datos"]["candidatos"]) == 2
    # todos los eventos siguen activos
    assert len(activos(cal)) == 3
    assert all(e["cancelado_en"] is None for e in cal.db.listar(incluir_cancelados=True))


def test_cancelacion_ambigua_se_resuelve_con_el_dia(cal):
    alta(cal, "Dentista", "2026-11-10T16:00")
    otro = alta(cal, "Dentista", "2026-11-24T10:00")
    r = cal.cancelar("dentista", "24 de noviembre")
    assert r["datos"]["cancelado"]["id"] == otro["id"]
    assert len(activos(cal)) == 1


def test_cancelar_tolera_erratas_y_tildes(cal):
    # Con erratas se encuentra el evento, pero solo se propone (QA A-01):
    # se cancela cuando el modelo vuelve con el id o con el título correcto.
    ev = alta(cal, "Revisión médica", "2026-11-12T11:00")
    alta(cal, "Fisio", "2026-11-05T08:30")
    r = cal.cancelar("revison medica")
    assert r["datos"]["cancelado"] is None and [c["id"] for c in r["datos"]["candidatos"]] == [ev["id"]]
    assert "'Revisión médica'" in r["texto"] and r["texto"].endswith("?")
    assert cal.cancelar(id=ev["id"])["datos"]["cancelado"]["id"] == ev["id"]
    # mayúsculas y tildes no son erratas: se cancela directamente
    ev2 = alta(cal, "Reunión con Lucía", "2026-11-13T11:00")
    assert cal.cancelar("REUNION CON LUCIA")["datos"]["cancelado"]["id"] == ev2["id"]
    ev3 = alta(cal, "Entrevista", "2026-11-16T12:00")
    r = cal.cancelar("la entrevsta")
    assert r["datos"]["cancelado"] is None and r["datos"]["candidatos"][0]["id"] == ev3["id"]


def test_cancelar_ignora_palabras_vacias(cal):
    ev = alta(cal, "Dentista", "2026-11-05T16:00")
    assert cal.cancelar("la cita del dentista")["datos"]["cancelado"]["id"] == ev["id"]


def test_cancelar_no_toca_eventos_pasados(cal):
    alta(cal, "Dentista", "2026-10-20T16:00")
    r = cal.cancelar("dentista")
    assert r["datos"]["cancelado"] is None
    assert len(activos(cal)) == 1


def test_cancelar_en_otro_dia_propone_sin_cancelar(cal):
    alta(cal, "Dentista", "2026-11-05T16:00")
    r = cal.cancelar("dentista", "viernes")
    assert r["datos"]["cancelado"] is None and len(r["datos"]["candidatos"]) == 1
    assert "el jueves 5" in r["texto"] and r["texto"].endswith("?")
    assert len(activos(cal)) == 1


def test_cancelar_solo_por_dia(cal):
    ev = alta(cal, "Fisio", "2026-11-05T08:30")
    assert cal.cancelar(dia="jueves")["datos"]["cancelado"]["id"] == ev["id"]
    assert "No tienes nada" in cal.cancelar(dia="viernes")["texto"]


def test_cancelar_por_id_y_sin_datos(cal):
    ev = alta(cal, "Fisio", "2026-11-05T08:30")
    assert cal.cancelar(id=ev["id"])["datos"]["cancelado"]["id"] == ev["id"]
    assert "No encuentro" in cal.cancelar(id=ev["id"])["texto"]  # ya cancelado
    assert cal.cancelar()["texto"] == "¿Qué evento quieres cancelar?"


def test_cancelar_fecha_no_entendida(cal):
    alta(cal, "Fisio", "2026-11-05T08:30")
    r = cal.cancelar("fisio", "algún día")
    assert r["texto"].endswith("?") and len(activos(cal)) == 1


# ── huecos ─────────────────────────────────────────────────────────────────

def test_huecos_alrededor_de_dos_eventos(cal):
    alta(cal, "Reunión", "2026-11-05T10:00", "2026-11-05T11:30")
    alta(cal, "Médico", "2026-11-05T16:00", "2026-11-05T17:00")
    alta(cal, "Fiesta", "2026-11-05", todo_el_dia=True)  # no ocupa franja
    r = cal.huecos("jueves", 60)
    assert r["datos"]["huecos"] == [{"inicio": "08:00", "fin": "10:00"},
                                    {"inicio": "11:30", "fin": "16:00"},
                                    {"inicio": "17:00", "fin": "21:00"}]
    assert r["texto"] == "El jueves 5 tienes libre de 08:00 a 10:00, de 11:30 a 16:00 y de 17:00 a 21:00."
    # con 3 horas solo cabe en las dos últimas franjas
    assert [h["inicio"] for h in cal.huecos("jueves", 180)["datos"]["huecos"]] == ["11:30", "17:00"]


def test_huecos_sin_sitio(cal):
    alta(cal, "Jornada", "2026-11-05T08:00", "2026-11-05T20:30")
    r = cal.huecos("jueves", "45")
    assert r["datos"]["huecos"] == []
    assert r["texto"] == ("No tienes ningún hueco de tres cuartos de hora el jueves 5 "
                          "entre las 08:00 y las 21:00.")


def test_huecos_hoy_empiezan_ahora(cal):
    r = cal.huecos("hoy", 30)
    assert r["datos"]["huecos"] == [{"inicio": "09:15", "fin": "21:00"}]


def test_huecos_franja_configurable(tmp_path):
    cal = Calendario(BaseDatos(tmp_path / "c.db"), "Europe/Madrid", reloj=lambda: AHORA,
                     huecos_desde="09:00", huecos_hasta="14:00")
    assert cal.huecos("jueves", 60)["datos"]["huecos"] == [{"inicio": "09:00", "fin": "14:00"}]


def test_huecos_errores(cal):
    assert cal.huecos("jueves", 0)["texto"].endswith("?")
    assert cal.huecos("jueves", "mucho")["texto"].endswith("?")
    assert cal.huecos("cuando sea", 30)["texto"].endswith("?")
    assert "ya ha pasado" in cal.huecos("2026-11-01", 30)["texto"]


# ── API y frontend ─────────────────────────────────────────────────────────

def test_alta_directa_valida(cal):
    r = cal.alta_directa("Fisio", "2026-11-05T08:30:00+01:00", "2026-11-05T09:30:00+01:00")
    assert r["evento"]["origen"] == "usuario" and r["solapamientos"] == []
    # UTC se convierte a la zona del módulo
    ev = cal.alta_directa("Vuelo", "2026-11-05T17:00:00Z")["evento"]
    assert ev["inicio"] == "2026-11-05T18:00:00+01:00"
    # solo fecha → día completo
    assert cal.alta_directa("Puente", "2026-11-09")["evento"]["todo_el_dia"] is True
    with pytest.raises(ErrorValidacion):
        cal.alta_directa("", "2026-11-05T08:30")
    with pytest.raises(ErrorValidacion):
        cal.alta_directa("Mal", "ayer por la tarde")
    with pytest.raises(ErrorValidacion):
        cal.alta_directa("Al revés", "2026-11-05T10:00", "2026-11-05T09:00")


def test_editar_conserva_duracion_y_avisa(cal):
    alta(cal, "Fisio", "2026-11-05T08:30", "2026-11-05T09:30")
    ev = alta(cal, "Clase", "2026-11-05T12:00", "2026-11-05T13:30")
    r = cal.editar(ev["id"], inicio="2026-11-05T09:00")
    assert r["evento"]["fin"] == "2026-11-05T10:30:00+01:00"
    assert [s["titulo"] for s in r["solapamientos"]] == ["Fisio"]
    r = cal.editar(ev["id"], titulo="Clase de yoga", ubicacion="Gimnasio")
    assert r["evento"]["titulo"] == "Clase de yoga" and r["evento"]["inicio"].startswith("2026-11-05T09:00")
    r = cal.editar(ev["id"], todo_el_dia=True)
    assert r["evento"]["todo_el_dia"] is True and r["evento"]["fin"] == "2026-11-06T00:00:00+01:00"
    with pytest.raises(NoEncontrado):
        cal.editar("no-existe", titulo="x")
    with pytest.raises(ErrorValidacion):
        cal.editar(ev["id"], origen="importado")


def test_cancelar_y_restaurar_por_id(cal):
    ev = alta(cal, "Fisio", "2026-11-05T08:30")
    assert cal.cancelar_por_id(ev["id"])["cancelado_en"] is not None
    assert cal.listar_rango("2026-11-05", "2026-11-05") == []
    assert len(cal.listar_rango("2026-11-05", "2026-11-05", incluir_cancelados=True)) == 1
    assert cal.restaurar(ev["id"])["cancelado_en"] is None
    assert len(cal.listar_rango("2026-11-05", "2026-11-05")) == 1
    with pytest.raises(NoEncontrado):
        cal.restaurar("no-existe")


def test_reset_valida_antes_de_borrar(cal):
    alta(cal, "Fisio", "2026-11-05T08:30")
    with pytest.raises(ErrorValidacion):
        cal.reset([{"titulo": "Bien", "inicio": "2026-11-06T10:00"}, {"titulo": "Mal"}])
    assert len(activos(cal)) == 1  # nada borrado
    n = cal.reset([{"id": "fijo-1", "titulo": "Cena", "inicio": "2026-11-06T21:00", "origen": "usuario"},
                   {"titulo": "Puente", "inicio": "2026-11-09", "todo_el_dia": True}])
    assert n == 2
    assert [e["titulo"] for e in activos(cal)] == ["Cena", "Puente"]
    assert cal.db.obtener("fijo-1")["origen"] == "usuario"


def test_reloj_sin_zona_se_interpreta_en_la_zona_del_modulo(tmp_path):
    cal = Calendario(BaseDatos(tmp_path / "c.db"), "Europe/Madrid",
                     reloj=lambda: datetime(2026, 11, 3, 23, 30))
    assert cal.crear("Tarde", "mañana", "10:00")["datos"]["evento"]["inicio"].startswith("2026-11-04")

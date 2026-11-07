"""Pruebas de la persistencia SQLite del Calendario (DEV-02)."""

from __future__ import annotations

import sqlite3

import pytest

from app.db import BaseDatos


def evento(id, titulo, inicio, fin=None, **extra):
    return {"id": id, "titulo": titulo, "inicio": inicio, "fin": fin,
            "todo_el_dia": extra.pop("todo_el_dia", False), "ubicacion": extra.pop("ubicacion", None),
            "notas": None, "origen": "usuario", "creado_en": "2026-10-29T18:00:00+01:00",
            "cancelado_en": None, **extra}


@pytest.fixture
def db(tmp_path):
    return BaseDatos(tmp_path / "datos" / "calendario.db")


def test_no_toca_el_disco_hasta_usarse(tmp_path):
    ruta = tmp_path / "nada" / "calendario.db"
    BaseDatos(ruta)
    assert not ruta.exists()


def test_crea_esquema_en_modo_wal(db):
    db.listar()
    con = sqlite3.connect(db.ruta)
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    tablas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tablas == {"eventos"}  # sin calendarios_externos (RF-07 recortado)
    columnas = {r[1] for r in con.execute("PRAGMA table_info(eventos)")}
    assert {"todo_el_dia", "cancelado_en", "origen"} <= columnas


def test_insertar_y_obtener(db):
    e = db.insertar(evento("a", "Fisio", "2026-10-30T08:30:00+01:00", "2026-10-30T09:30:00+01:00",
                           ubicacion="Clínica"))
    assert e["titulo"] == "Fisio" and e["ubicacion"] == "Clínica"
    assert e["todo_el_dia"] is False and e["cancelado_en"] is None
    assert db.obtener("a") == e
    assert db.obtener("no-existe") is None


def test_todo_el_dia_es_booleano(db):
    db.insertar(evento("a", "Cumpleaños", "2026-10-31T00:00:00+01:00",
                       "2026-11-01T00:00:00+01:00", todo_el_dia=True))
    assert db.obtener("a")["todo_el_dia"] is True


def test_listar_por_rango_ordenado(db):
    db.insertar(evento("b", "Cena", "2026-10-30T21:00:00+01:00", "2026-10-30T22:00:00+01:00"))
    db.insertar(evento("a", "Fisio", "2026-10-30T08:30:00+01:00", "2026-10-30T09:30:00+01:00"))
    db.insertar(evento("c", "Otro día", "2026-10-31T10:00:00+01:00", "2026-10-31T11:00:00+01:00"))
    assert [e["id"] for e in db.listar("2026-10-30", "2026-10-31")] == ["a", "b"]
    assert [e["id"] for e in db.listar()] == ["a", "b", "c"]


def test_listar_rango_que_cruza_de_mes(db):
    db.insertar(evento("oct", "Halloween", "2026-10-31T20:00:00+01:00", "2026-10-31T23:00:00+01:00"))
    db.insertar(evento("nov", "Todos los Santos", "2026-11-01T00:00:00+01:00",
                       "2026-11-02T00:00:00+01:00", todo_el_dia=True))
    db.insertar(evento("fuera", "Más tarde", "2026-11-03T10:00:00+01:00", "2026-11-03T11:00:00+01:00"))
    assert [e["id"] for e in db.listar("2026-10-30", "2026-11-02")] == ["oct", "nov"]


def test_listar_incluye_eventos_que_empiezan_antes_del_rango(db):
    db.insertar(evento("noche", "Guardia", "2026-10-29T22:00:00+01:00", "2026-10-30T02:00:00+01:00"))
    assert [e["id"] for e in db.listar("2026-10-30", "2026-10-31")] == ["noche"]
    # y el día completo que termina justo al empezar el rango no entra
    db.insertar(evento("ayer", "Ayer", "2026-10-29T00:00:00+01:00", "2026-10-30T00:00:00+01:00",
                       todo_el_dia=True))
    assert "ayer" not in [e["id"] for e in db.listar("2026-10-30", "2026-10-31")]


def test_listar_excluye_cancelados_salvo_que_se_pidan(db):
    db.insertar(evento("a", "Fisio", "2026-10-30T08:30:00+01:00", "2026-10-30T09:30:00+01:00"))
    db.insertar(evento("b", "Cena", "2026-10-30T21:00:00+01:00", "2026-10-30T22:00:00+01:00"))
    db.cancelar("a", "2026-10-29T18:05:00+01:00")
    assert [e["id"] for e in db.listar("2026-10-30", "2026-10-31")] == ["b"]
    assert [e["id"] for e in db.listar("2026-10-30", "2026-10-31", incluir_cancelados=True)] == ["a", "b"]


def test_actualizar(db):
    db.insertar(evento("a", "Fisio", "2026-10-30T08:30:00+01:00", "2026-10-30T09:30:00+01:00"))
    e = db.actualizar("a", titulo="Fisioterapia", inicio="2026-10-30T09:00:00+01:00",
                      fin="2026-10-30T10:00:00+01:00")
    assert e["titulo"] == "Fisioterapia" and e["inicio"].startswith("2026-10-30T09:00")
    assert db.actualizar("no-existe", titulo="x") is None
    with pytest.raises(ValueError):
        db.actualizar("a", creado_en="otro")


def test_cancelar_y_restaurar(db):
    db.insertar(evento("a", "Fisio", "2026-10-30T08:30:00+01:00"))
    e = db.cancelar("a", "2026-10-29T18:05:00+01:00")
    assert e["cancelado_en"] == "2026-10-29T18:05:00+01:00"
    # cancelar dos veces conserva la primera fecha
    assert db.cancelar("a", "2026-10-29T19:00:00+01:00")["cancelado_en"] == "2026-10-29T18:05:00+01:00"
    assert db.restaurar("a")["cancelado_en"] is None
    assert db.cancelar("no-existe", "x") is None


def test_vaciar(db):
    db.insertar(evento("a", "Fisio", "2026-10-30T08:30:00+01:00"))
    db.vaciar()
    assert db.listar(incluir_cancelados=True) == []


def test_migra_base_sin_todo_el_dia(tmp_path):
    ruta = tmp_path / "vieja.db"
    con = sqlite3.connect(ruta)
    con.execute("CREATE TABLE eventos (id TEXT PRIMARY KEY, titulo TEXT NOT NULL, inicio TEXT NOT NULL,"
                " fin TEXT, ubicacion TEXT, notas TEXT, origen TEXT, creado_en TEXT NOT NULL,"
                " cancelado_en TEXT)")
    con.execute("INSERT INTO eventos (id, titulo, inicio, creado_en) VALUES ('v', 'Vieja',"
                " '2026-10-30T10:00:00+01:00', '2026-10-01T00:00:00+02:00')")
    con.commit(); con.close()
    assert BaseDatos(ruta).obtener("v")["todo_el_dia"] is False

"""Pruebas del generador .ics (DEV-04). icalendar solo se usa para validar."""

from __future__ import annotations

from datetime import date, datetime, timezone

import icalendar

from app.ics import escapar, generar, plegar

AHORA = datetime(2026, 11, 3, 8, 10, tzinfo=timezone.utc)


def ev(id, titulo, inicio, fin, **kw):
    return {"id": id, "titulo": titulo, "inicio": inicio, "fin": fin,
            "todo_el_dia": kw.get("todo_el_dia", False), "ubicacion": kw.get("ubicacion"),
            "notas": kw.get("notas"), "origen": "usuario",
            "creado_en": "2026-11-01T10:00:00+01:00", "cancelado_en": kw.get("cancelado_en")}


EVENTOS = [
    ev("11111111-1111-4111-8111-111111111111", "Dentista, revisión; anual", "2026-11-05T16:00:00+01:00",
       "2026-11-05T17:00:00+01:00", ubicacion="Calle Ñandú, 3", notas="Llevar informe\nY la tarjeta"),
    ev("22222222-2222-4222-8222-222222222222", "Puente", "2026-11-09T00:00:00+01:00",
       "2026-11-10T00:00:00+01:00", todo_el_dia=True),
    ev("33333333-3333-4333-8333-333333333333", "Cancelado", "2026-11-06T10:00:00+01:00",
       "2026-11-06T11:00:00+01:00", cancelado_en="2026-11-02T10:00:00+01:00"),
    ev("44444444-4444-4444-8444-444444444444", "Reunión " + "muy " * 30 + "larga con tildes: áéíóú",
       "2026-11-07T09:00:00+01:00", "2026-11-07T10:00:00+01:00"),
]


def test_cabecera_y_crlf():
    salida = generar(EVENTOS, nombre="Agenda de Enric", ahora=AHORA, zona="Europe/Madrid")
    assert salida.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:")
    assert salida.endswith("END:VCALENDAR\r\n")
    assert "X-WR-CALNAME:Agenda de Enric\r\n" in salida
    # todas las líneas terminan en CRLF: no queda ningún \n suelto
    assert "\n" not in salida.replace("\r\n", "")


def test_se_parsea_y_los_uid_coinciden():
    cal = icalendar.Calendar.from_ical(generar(EVENTOS, ahora=AHORA))
    eventos = cal.walk("VEVENT")
    uids = [str(e["UID"]) for e in eventos]
    assert uids == [EVENTOS[0]["id"], EVENTOS[1]["id"], EVENTOS[3]["id"]]  # sin el cancelado


def test_horas_en_utc_y_dia_completo_con_fecha():
    salida = generar(EVENTOS, ahora=AHORA)
    assert "DTSTART:20261105T150000Z" in salida and "DTEND:20261105T160000Z" in salida
    assert "DTSTART;VALUE=DATE:20261109" in salida and "DTEND;VALUE=DATE:20261110" in salida
    assert "DTSTAMP:20261103T081000Z" in salida
    cal = icalendar.Calendar.from_ical(salida)
    puente = cal.walk("VEVENT")[1]
    assert puente.decoded("DTSTART") == date(2026, 11, 9)
    dentista = cal.walk("VEVENT")[0]
    assert dentista.decoded("DTSTART") == datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc)


def test_ida_y_vuelta_de_comas_puntos_y_coma_y_tildes():
    cal = icalendar.Calendar.from_ical(generar(EVENTOS, ahora=AHORA))
    dentista = cal.walk("VEVENT")[0]
    assert str(dentista["SUMMARY"]) == "Dentista, revisión; anual"
    assert str(dentista["LOCATION"]) == "Calle Ñandú, 3"
    assert str(dentista["DESCRIPTION"]) == "Llevar informe\nY la tarjeta"


def test_lineas_largas_plegadas_a_75_octetos():
    salida = generar(EVENTOS, ahora=AHORA)
    lineas = salida.split("\r\n")
    assert all(len(l.encode("utf-8")) <= 75 for l in lineas)
    assert any(l.startswith(" ") for l in lineas)  # hay continuaciones
    cal = icalendar.Calendar.from_ical(salida)
    assert str(cal.walk("VEVENT")[2]["SUMMARY"]) == EVENTOS[3]["titulo"]


def test_plegar_no_parte_caracteres_multibyte():
    linea = "SUMMARY:" + "ñ" * 80
    plegada = plegar(linea)
    for trozo in plegada.split("\r\n"):
        trozo.encode("utf-8").decode("utf-8")  # no lanza
        assert len(trozo.encode("utf-8")) <= 75
    assert plegada.replace("\r\n ", "") == linea


def test_escapar():
    assert escapar("a\\b;c,d\ne") == "a\\\\b\\;c\\,d\\ne"


def test_calendario_vacio_es_valido():
    cal = icalendar.Calendar.from_ical(generar([], ahora=AHORA))
    assert cal.walk("VEVENT") == []

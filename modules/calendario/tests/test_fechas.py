"""Pruebas del resolvedor de fechas con la fecha de referencia del spike:
lunes 12/10/2026."""

from datetime import date, datetime, time
import importlib.util
from pathlib import Path

import pytest

_ruta = Path(__file__).resolve().parents[1] / "app" / "fechas.py"
_spec = importlib.util.spec_from_file_location("fechas", _ruta)
f = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(f)

HOY = date(2026, 10, 12)  # lunes


@pytest.mark.parametrize("expr,esperado", [
    ("hoy", date(2026, 10, 12)),
    ("mañana", date(2026, 10, 13)),
    ("Mañana", date(2026, 10, 13)),
    ("pasado mañana", date(2026, 10, 14)),
    ("martes", date(2026, 10, 13)),
    ("el martes", date(2026, 10, 13)),
    ("el martes que viene", date(2026, 10, 13)),
    ("miércoles", date(2026, 10, 14)),
    ("miercoles", date(2026, 10, 14)),
    ("el jueves", date(2026, 10, 15)),
    ("este viernes", date(2026, 10, 16)),
    ("sábado", date(2026, 10, 17)),
    ("domingo", date(2026, 10, 18)),
    ("lunes", date(2026, 10, 19)),      # dicho un lunes: el de la semana que viene
    ("2026-11-03", date(2026, 11, 3)),
    ("2026-11-03T16:15", date(2026, 11, 3)),
    ("3 de noviembre", date(2026, 11, 3)),
    ("el 11 de noviembre", date(2026, 11, 11)),
    ("3/11", date(2026, 11, 3)),
    ("5 de enero", date(2027, 1, 5)),   # ya pasado este año → año siguiente
    ("algún día", None),
    ("", None),
])
def test_resolver_dia(expr, esperado):
    assert f.resolver_dia(expr, HOY) == esperado


@pytest.mark.parametrize("expr,esperado", [
    ("09:00", time(9, 0)), ("9", time(9, 0)), ("17:00", time(17, 0)),
    ("21:30", time(21, 30)), ("21.30", time(21, 30)), ("19h", time(19, 0)),
    ("25:00", None), ("tarde", None), (None, None),
])
def test_resolver_hora(expr, esperado):
    assert f.resolver_hora(expr) == esperado


@pytest.mark.parametrize("expr,desde,hasta", [
    ("hoy", date(2026, 10, 12), date(2026, 10, 12)),
    ("mañana", date(2026, 10, 13), date(2026, 10, 13)),
    ("esta semana", date(2026, 10, 12), date(2026, 10, 18)),
    ("la semana que viene", date(2026, 10, 19), date(2026, 10, 25)),
    ("próxima semana", date(2026, 10, 19), date(2026, 10, 25)),
    ("fin de semana", date(2026, 10, 17), date(2026, 10, 18)),
    ("este mes", date(2026, 10, 12), date(2026, 10, 31)),
    ("el mes que viene", date(2026, 11, 1), date(2026, 11, 30)),
    ("viernes", date(2026, 10, 16), date(2026, 10, 16)),
])
def test_resolver_periodo(expr, desde, hasta):
    assert f.resolver_periodo(expr, HOY) == (desde, hasta)


def test_resolver_momento():
    assert f.resolver_momento("sábado", "21:30", HOY) == datetime(2026, 10, 17, 21, 30)
    assert f.resolver_momento("11 de noviembre", None, HOY) == datetime(2026, 11, 11, 0, 0)
    assert f.resolver_momento("nunca", "10:00", HOY) is None

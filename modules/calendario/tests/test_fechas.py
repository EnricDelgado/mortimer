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


# ── DEV-01 (Incremento 1): erratas, mediodía/medianoche y reloj con zona ──
# Frases propias, no tomadas del banco de evaluación.

@pytest.mark.parametrize("expr,esperado", [
    ("vierns", date(2026, 10, 16)),
    ("el juevs", date(2026, 10, 15)),
    ("miercles", date(2026, 10, 14)),
    ("sabdo", date(2026, 10, 17)),
    ("domnigo", date(2026, 10, 18)),
    ("manana", date(2026, 10, 13)),
    ("mañna", date(2026, 10, 13)),
    ("pasdo mañana", date(2026, 10, 14)),
    ("4 de novimbre", date(2026, 11, 4)),
    ("nunca", None),
    ("algún rato", None),
])
def test_resolver_dia_con_erratas(expr, esperado):
    assert f.resolver_dia(expr, HOY) == esperado


@pytest.mark.parametrize("expr,desde,hasta", [
    ("sémana que viene", date(2026, 10, 19), date(2026, 10, 25)),
    ("la semna que viene", date(2026, 10, 19), date(2026, 10, 25)),
    ("la semana que vienne", date(2026, 10, 19), date(2026, 10, 25)),
    ("esta smana", date(2026, 10, 12), date(2026, 10, 18)),
    ("el finde", date(2026, 10, 17), date(2026, 10, 18)),
    ("el mes que biene", date(2026, 11, 1), date(2026, 11, 30)),
    ("vierns", date(2026, 10, 16), date(2026, 10, 16)),
])
def test_resolver_periodo_con_erratas(expr, desde, hasta):
    assert f.resolver_periodo(expr, HOY) == (desde, hasta)


@pytest.mark.parametrize("expr,esperado", [
    ("mediodia", time(12, 0)),
    ("a mediodía", time(12, 0)),
    ("las 12 del mediodía", time(12, 0)),
    ("medianoche", time(0, 0)),
    ("a medianoche", time(0, 0)),
    ("mediodoa", time(12, 0)),
    ("mañana", None),
])
def test_resolver_hora_palabras(expr, esperado):
    assert f.resolver_hora(expr) == esperado


def test_resolver_dia_con_reloj():
    from zoneinfo import ZoneInfo
    madrid = ZoneInfo("Europe/Madrid")
    # jueves 29/10/2026 a las 23:30 en Madrid: en UTC ya sería otro día.
    ahora = datetime(2026, 10, 29, 23, 30, tzinfo=madrid)
    assert f.resolver_dia_con_reloj("hoy", ahora) == date(2026, 10, 29)
    assert f.resolver_dia_con_reloj("mañana", ahora) == date(2026, 10, 30)
    assert f.resolver_dia_con_reloj("vierns", ahora) == date(2026, 10, 30)
    assert f.resolver_dia_con_reloj("jueves", ahora) == date(2026, 11, 5)
    assert f.resolver_dia_con_reloj("xyz", ahora) is None


def test_resolver_dia_con_reloj_exige_zona():
    with pytest.raises(ValueError):
        f.resolver_dia_con_reloj("hoy", datetime(2026, 10, 29, 18, 0))

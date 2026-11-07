"""Pruebas del frontend del Calendario (DEV-15, DEV-16) y del despliegue (DEV-17).

Reloj fijo: jueves 29/10/2026 a las 18:00 en Madrid. La semana actual va del
lunes 26 de octubre al domingo 1 de noviembre, así que cruza de mes.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import icalendar
import pytest
from starlette.testclient import TestClient

from app.db import BaseDatos
from app.dominio import Calendario
from app.main import crear_app, crear_servidor
from app.web import raiz_desde_ruta

MADRID = ZoneInfo("Europe/Madrid")
AHORA = datetime(2026, 10, 29, 18, 0, tzinfo=MADRID)
HTMX = {"HX-Request": "true"}
MODULO = Path(__file__).resolve().parents[1]
REPO = MODULO.parents[1]


@pytest.fixture
def servicio(tmp_path):
    return Calendario(BaseDatos(tmp_path / "calendario.db"), MADRID, reloj=lambda: AHORA)


@pytest.fixture
def cliente(servicio):
    app = crear_app(crear_servidor(servicio), hosts="testserver,localhost:*,calendario:*")
    return TestClient(app)


def alta(cliente, **datos):
    r = cliente.post("/api/eventos", json=datos)
    assert r.status_code == 201, r.text
    return r.json()["evento"]


def vista(html: str) -> str:
    """Solo la rejilla de la semana o el mes (sin el formulario de alta ni los cancelados)."""
    m = re.search(r'<(?:ol class="semana"|table class="mes").*?</(?:ol|table)>', html, re.S)
    assert m, "no hay rejilla en la página"
    return m.group(0)


def cancelados(html: str) -> str:
    return html.split('<section class="cancelados"', 1)[1].split("</section>", 1)[0]


def dia(html: str, fecha: str) -> str:
    """Bloque de un día en la vista de semana."""
    m = re.search(rf'<li class="dia[^"]*" id="d-{fecha}".*?(?=<li class="dia|</ol>)', html, re.S)
    assert m, f"no está el día {fecha}"
    return m.group(0)


def celda(html: str, fecha: str) -> str:
    """Celda de un día en la vista de mes."""
    m = re.search(rf'<td[^>]*>\s*<a class="num" href="[^"]*semana={fecha}#.*?</td>', html, re.S)
    assert m, f"no está la celda {fecha}"
    return m.group(0)


def eventos_ics(cliente) -> dict[str, icalendar.Event]:
    r = cliente.get("/calendario.ics")
    assert r.status_code == 200
    cal = icalendar.Calendar.from_ical(r.content)
    return {str(e["SUMMARY"]): e for e in cal.walk("VEVENT")}


URL_ABSOLUTA = re.compile(
    r'\b(?:href|src|action|hx-get|hx-post|hx-put|hx-patch|hx-delete|data-ruta)\s*=\s*["\']\s*(?:/|[a-z]+:)', re.I)


# ── DEV-15 · Vistas semana y mes ──────────────────────────────────────────

def test_raiz_relativa_segun_profundidad():
    assert raiz_desde_ruta("/ui") == ""
    assert raiz_desde_ruta("/ui/mes") == "../"
    assert raiz_desde_ruta("/ui/") == "../"
    assert raiz_desde_ruta("/ui/eventos/abc/cancelar") == "../../../"


def test_ui_muestra_la_semana_del_reloj_fijo(cliente):
    alta(cliente, titulo="Antes", inicio="2026-10-25T10:00")          # domingo anterior
    alta(cliente, titulo="Reunión", inicio="2026-10-26T10:00", fin="2026-10-26T11:30")
    alta(cliente, titulo="Fisio", inicio="2026-10-30T08:30", ubicacion="Clínica Sol")
    alta(cliente, titulo="Excursión", inicio="2026-11-01", todo_el_dia=True)
    alta(cliente, titulo="Después", inicio="2026-11-02T09:00")        # lunes siguiente
    r = cliente.get("/ui")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    html = r.text
    assert "26 de octubre – 1 de noviembre de 2026" in html
    rejilla = vista(html)
    assert "Antes" not in rejilla and "Después" not in rejilla
    assert "10:00–11:30" in dia(html, "2026-10-26") and "Reunión" in dia(html, "2026-10-26")
    assert "Fisio" in dia(html, "2026-10-30") and "Clínica Sol" in dia(html, "2026-10-30")
    assert "Excursión" in dia(html, "2026-11-01") and "Todo el día" in dia(html, "2026-11-01")
    assert 'aria-current="date"' in dia(html, "2026-10-29")
    assert "Libre" in dia(html, "2026-10-27")


def test_navegacion_entre_semanas(cliente):
    alta(cliente, titulo="Dentista", inicio="2026-11-03T16:00")
    html = cliente.get("/ui", params={"semana": "2026-11-04"}).text
    assert "2 – 8 de noviembre de 2026" in html
    assert 'href="ui?semana=2026-10-26"' in html and 'href="ui?semana=2026-11-09"' in html
    assert "Dentista" in dia(html, "2026-11-03")
    # Hoy siempre lleva a la semana del reloj; el cambio de vista conserva la fecha.
    assert 'href="ui"' in html and 'href="ui/mes?mes=2026-11"' in html


def test_semana_que_cruza_de_anio(cliente):
    html = cliente.get("/ui", params={"semana": "2027-01-01"}).text
    assert "28 de diciembre de 2026 – 3 de enero de 2027" in html


def test_evento_de_varios_dias_y_que_cruza_medianoche(cliente):
    alta(cliente, titulo="Puente", inicio="2026-10-31", fin="2026-11-03", todo_el_dia=True)
    alta(cliente, titulo="Concierto", inicio="2026-10-30T23:00", fin="2026-10-31T01:00")
    html = cliente.get("/ui").text
    assert "Puente" not in dia(html, "2026-10-30")
    assert "Puente" in dia(html, "2026-10-31") and "Puente" in dia(html, "2026-11-01")
    assert "hasta el lunes 2" in dia(html, "2026-10-31")
    assert "23:00–01:00 (+1)" in dia(html, "2026-10-30")
    assert "hasta las 01:00" in dia(html, "2026-10-31")
    # El fin de día completo es exclusivo: el martes 3 ya no aparece.
    siguiente = cliente.get("/ui", params={"semana": "2026-11-02"}).text
    assert "Puente" in dia(siguiente, "2026-11-02") and "Puente" not in dia(siguiente, "2026-11-03")


def test_vista_de_mes_cruza_bien_de_mes(cliente):
    alta(cliente, titulo="Halloween", inicio="2026-10-31T20:00")
    alta(cliente, titulo="Puente", inicio="2026-10-31", fin="2026-11-03", todo_el_dia=True)
    alta(cliente, titulo="Médico", inicio="2026-11-17T09:30")
    alta(cliente, titulo="Diciembre", inicio="2026-12-02T10:00")
    alta(cliente, titulo="Fuera de rejilla", inicio="2026-12-10T10:00")
    r = cliente.get("/ui/mes", params={"mes": "2026-11"})
    assert r.status_code == 200
    html = r.text
    assert "Noviembre de 2026" in html
    rejilla = vista(html)
    # Noviembre de 2026 empieza en domingo: la rejilla va del lunes 26/10 al domingo 6/12.
    assert re.findall(r"semana=(\d{4}-\d{2}-\d{2})#", rejilla)[0] == "2026-10-26"
    assert re.findall(r"semana=(\d{4}-\d{2}-\d{2})#", rejilla)[-1] == "2026-12-06"
    assert len(re.findall(r"<tr>", rejilla)) == 1 + 6   # cabecera + 6 semanas
    assert "Halloween" in celda(html, "2026-10-31") and 'class="fuera' in celda(html, "2026-10-31")
    assert all("Puente" in celda(html, f) for f in ("2026-10-31", "2026-11-01", "2026-11-02"))
    assert "Puente" not in celda(html, "2026-11-03")
    assert "09:30" in celda(html, "2026-11-17") and "Médico" in celda(html, "2026-11-17")
    assert "Diciembre" in celda(html, "2026-12-02")
    assert "Fuera de rejilla" not in rejilla
    assert 'href="../ui/mes?mes=2026-10"' in html and 'href="../ui/mes?mes=2026-12"' in html


def test_mes_cambia_de_anio_y_mes_por_defecto(cliente):
    dic = cliente.get("/ui/mes", params={"mes": "2026-12"}).text
    assert "Diciembre de 2026" in dic and 'href="../ui/mes?mes=2027-01"' in dic
    ene = cliente.get("/ui/mes", params={"mes": "2027-01"}).text
    assert 'href="../ui/mes?mes=2026-12"' in ene
    actual = cliente.get("/ui/mes").text
    assert "Octubre de 2026" in actual and 'aria-current="date"' in celda(actual, "2026-10-29")


def test_mes_con_muchos_eventos_resume_la_celda(cliente):
    for h in ("08:00", "10:00", "12:00", "16:00", "18:00"):
        alta(cliente, titulo=f"Cita {h}", inicio=f"2026-10-30T{h}")
    c = celda(cliente.get("/ui/mes").text, "2026-10-30")
    assert "Cita 12:00" in c and "Cita 16:00" not in c and "+2 más" in c


def test_parametros_no_validos_vuelven_al_presente(cliente):
    r = cliente.get("/ui", params={"semana": "ayer"})
    assert r.status_code == 200 and "26 de octubre – 1 de noviembre de 2026" in r.text
    assert 'class="aviso error"' in r.text
    r = cliente.get("/ui/mes", params={"mes": "2026-13"})
    assert r.status_code == 200 and "Octubre de 2026" in r.text and 'class="aviso error"' in r.text


def test_no_hay_urls_absolutas(cliente):
    ev = alta(cliente, titulo="Fisio", inicio="2026-10-30T08:30")
    cliente.post(f"/api/eventos/{alta(cliente, titulo='Viejo', inicio='2026-10-28T08:30')['id']}/cancelar")
    paginas = [
        cliente.get("/ui").text,
        cliente.get("/ui", params={"semana": "2026-11-09"}).text,
        cliente.get("/ui/mes").text,
        cliente.get("/ui/mes", params={"mes": "2026-11"}).text,
        cliente.post(f"/ui/eventos/{ev['id']}", data={"titulo": "Fisio", "fecha": "2026-10-30", "hora": "09:00",
                                                     "raiz": "", "volver": "semana:2026-10-26"}, headers=HTMX).text,
        cliente.post("/ui/eventos", data={"titulo": "Sin JS", "fecha": "2026-10-30"}).text,
    ]
    for html in paginas:
        assert 'href="/' not in html and 'src="/' not in html
        assert not URL_ABSOLUTA.findall(html), URL_ABSOLUTA.findall(html)
        assert "http://" not in html and "https://" not in html
    # Los enlaces se adaptan a la profundidad de la página.
    assert 'src="ui/static/htmx.min.js"' in paginas[0]
    assert 'src="../ui/static/htmx.min.js"' in paginas[2]


def test_htmx_servido_por_el_modulo(cliente):
    r = cliente.get("/ui/static/htmx.min.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    assert r.content == (MODULO / "app/static/htmx.min.js").read_bytes()
    assert cliente.get("/ui/static/web.py").status_code == 404
    assert cliente.get("/ui/static/..%2Fweb.py").status_code == 404


def test_cancelados_aparecen_en_su_lista(cliente):
    ev = alta(cliente, titulo="Peluquería", inicio="2026-10-30T17:00")
    cliente.post(f"/api/eventos/{ev['id']}/cancelar")
    html = cliente.get("/ui").text
    assert "Peluquería" not in vista(html)
    lista = cancelados(html)
    assert "Peluquería" in lista and "viernes 30 de octubre a las 17:00" in lista
    assert f'hx-post="ui/eventos/{ev["id"]}/restaurar"' in lista and "Restaurar" in lista
    assert "Peluquería" in cancelados(cliente.get("/ui/mes").text)


def test_ui_respeta_allowed_hosts(cliente):
    assert cliente.get("/ui", headers={"Host": "malicioso.com"}).status_code == 421
    assert cliente.post("/ui/eventos", data={"titulo": "x", "fecha": "2026-10-30"},
                        headers={"Host": "malicioso.com"}).status_code == 421


# ── DEV-16 · Alta, edición y cancelación manuales ─────────────────────────

def test_alta_aparece_en_ui_y_en_ics(cliente):
    r = cliente.post("/ui/eventos", headers=HTMX, data={
        "titulo": "Cena con Marta", "fecha": "2026-10-30", "hora": "21:00", "hora_fin": "23:00",
        "ubicacion": "Casa", "notas": "Llevar postre", "raiz": "", "volver": "semana:2026-10-26"})
    assert r.status_code == 200
    # Con htmx se devuelve solo el contenido de #vista, con el aviso.
    assert "<html" not in r.text and 'id="vista"' not in r.text
    assert "Añadido: &#39;Cena con Marta&#39; el viernes 30 de octubre a las 21:00." in r.text
    assert "Cena con Marta" in dia(r.text, "2026-10-30")
    html = cliente.get("/ui").text
    assert "21:00–23:00" in dia(html, "2026-10-30") and "Llevar postre" in dia(html, "2026-10-30")
    ev = eventos_ics(cliente)["Cena con Marta"]
    assert ev["DTSTART"].dt == datetime(2026, 10, 30, 21, 0, tzinfo=MADRID)
    assert ev["DTEND"].dt == datetime(2026, 10, 30, 23, 0, tzinfo=MADRID)
    assert str(ev["LOCATION"]) == "Casa"
    guardado = cliente.get("/api/eventos", params={"desde": "2026-10-30", "hasta": "2026-10-30"}).json()
    assert guardado[0]["origen"] == "usuario"


def test_alta_en_otra_semana_lleva_a_esa_semana(cliente):
    r = cliente.post("/ui/eventos", headers=HTMX, data={
        "titulo": "Revisión ITV", "fecha": "2026-11-12", "hora": "10:00", "raiz": "", "volver": "semana:2026-10-26"})
    assert "9 – 15 de noviembre de 2026" in r.text and "Revisión ITV" in dia(r.text, "2026-11-12")
    assert "10:00–11:00" in dia(r.text, "2026-11-12")   # sin «hasta», una hora
    r = cliente.post("/ui/eventos", headers=HTMX, data={
        "titulo": "Navidad", "fecha": "2026-12-25", "raiz": "../", "volver": "mes:2026-10"})
    assert "Diciembre de 2026" in r.text and 'href="../ui/mes?mes=2027-01"' in r.text


def test_alta_sin_hora_es_de_dia_completo(cliente):
    cliente.post("/ui/eventos", data={"titulo": "Mudanza", "fecha": "2026-10-31", "hora_fin": "12:00"})
    cliente.post("/ui/eventos", data={"titulo": "Boda", "fecha": "2026-10-31", "hora": "13:00",
                                      "todo_el_dia": "1"})
    eventos = cliente.get("/api/eventos", params={"desde": "2026-10-31", "hasta": "2026-10-31"}).json()
    assert {e["titulo"]: e["todo_el_dia"] for e in eventos} == {"Mudanza": True, "Boda": True}
    assert all(e["fin"].startswith("2026-11-01T00:00") for e in eventos)
    ics = eventos_ics(cliente)
    assert str(ics["Mudanza"]["DTSTART"].dt) == "2026-10-31"


def test_alta_que_cruza_medianoche(cliente):
    cliente.post("/ui/eventos", data={"titulo": "Guardia", "fecha": "2026-10-30", "hora": "23:00", "hora_fin": "01:00"})
    ev = cliente.get("/api/eventos", params={"desde": "2026-10-30", "hasta": "2026-10-30"}).json()[0]
    assert ev["fin"] == "2026-10-31T01:00:00+01:00"


def test_alta_con_solapamiento_avisa(cliente):
    alta(cliente, titulo="Fisio", inicio="2026-10-30T08:30")
    r = cliente.post("/ui/eventos", headers=HTMX, data={
        "titulo": "Café", "fecha": "2026-10-30", "hora": "09:00", "raiz": "", "volver": "semana:2026-10-26"})
    assert 'class="aviso atencion"' in r.text and "Ojo: se solapa con &#39;Fisio&#39; (08:30–09:30)" in r.text
    assert "Café" in dia(r.text, "2026-10-30")   # se crea igualmente


def test_alta_no_valida_no_crea_nada_y_conserva_el_formulario(cliente):
    datos = {"titulo": "", "fecha": "2026-10-30", "hora": "10:00", "ubicacion": "Ayuntamiento",
             "raiz": "", "volver": "semana:2026-10-26"}
    r = cliente.post("/ui/eventos", headers=HTMX, data=datos)
    assert r.status_code == 200   # htmx solo intercambia respuestas 2xx
    assert 'class="aviso error"' in r.text and "Falta el título" in r.text
    assert '<details id="alta" class="alta" open>' in r.text and 'value="Ayuntamiento"' in r.text
    r = cliente.post("/ui/eventos", data={**datos, "titulo": "x", "fecha": "el jueves"})
    assert r.status_code == 422 and "<html" in r.text and "Falta la fecha" in r.text
    r = cliente.post("/ui/eventos", data={**datos, "titulo": "x", "hora": "a las tantas"})
    assert r.status_code == 422 and "No entiendo la hora" in r.text
    assert cliente.get("/api/eventos").json() == []


def test_edicion_de_la_hora_cambia_en_ui_y_en_ics(cliente):
    ev = alta(cliente, titulo="Dentista", inicio="2026-10-30T16:00", ubicacion="Calle Mayor")
    r = cliente.post(f"/ui/eventos/{ev['id']}", headers=HTMX, data={
        "titulo": "Dentista", "fecha": "2026-10-30", "hora": "17:30", "hora_fin": "18:15",
        "ubicacion": "Calle Mayor", "raiz": "", "volver": "semana:2026-10-26"})
    assert r.status_code == 200 and "Guardado:" in r.text
    html = cliente.get("/ui").text
    assert "17:30–18:15" in dia(html, "2026-10-30") and "16:00" not in dia(html, "2026-10-30")
    ics = eventos_ics(cliente)["Dentista"]
    assert ics["DTSTART"].dt == datetime(2026, 10, 30, 17, 30, tzinfo=MADRID)
    assert ics["DTEND"].dt == datetime(2026, 10, 30, 18, 15, tzinfo=MADRID)
    assert str(ics["UID"]) == ev["id"]


def test_edicion_cambia_de_dia_titulo_y_quita_la_ubicacion(cliente):
    ev = alta(cliente, titulo="Dentista", inicio="2026-10-30T16:00", ubicacion="Calle Mayor")
    r = cliente.post(f"/ui/eventos/{ev['id']}", headers=HTMX, data={
        "titulo": "Dentista (revisión)", "fecha": "2026-11-05", "hora": "16:00", "hora_fin": "17:00",
        "ubicacion": "", "raiz": "", "volver": "semana:2026-10-26"})
    assert "2 – 8 de noviembre de 2026" in r.text and "Dentista (revisión)" in dia(r.text, "2026-11-05")
    guardado = cliente.get(f"/api/eventos/{ev['id']}").json()
    assert guardado["ubicacion"] is None and guardado["inicio"] == "2026-11-05T16:00:00+01:00"
    assert "LOCATION" not in eventos_ics(cliente)["Dentista (revisión)"]


def test_edicion_conserva_eventos_de_varios_dias(cliente):
    ev = alta(cliente, titulo="Vacaciones", inicio="2026-11-02", fin="2026-11-07", todo_el_dia=True)
    cliente.post(f"/ui/eventos/{ev['id']}", data={"titulo": "Vacaciones en Menorca", "fecha": "2026-11-03",
                                                  "todo_el_dia": "1"})
    guardado = cliente.get(f"/api/eventos/{ev['id']}").json()
    assert guardado["inicio"].startswith("2026-11-03T00:00") and guardado["fin"].startswith("2026-11-08T00:00")


def test_edicion_errores(cliente):
    ev = alta(cliente, titulo="Fisio", inicio="2026-10-30T08:30")
    r = cliente.post(f"/ui/eventos/{ev['id']}", headers=HTMX, data={"titulo": "Fisio", "fecha": "",
                                                                   "raiz": "", "volver": "semana:2026-10-26"})
    assert 'class="aviso error"' in r.text and "No he podido guardar" in r.text
    assert cliente.get(f"/api/eventos/{ev['id']}").json()["inicio"] == ev["inicio"]
    r = cliente.post("/ui/eventos/no-existe", headers=HTMX, data={"titulo": "x", "fecha": "2026-10-30",
                                                                "raiz": "", "volver": ""})
    assert "Ese evento ya no existe" in r.text
    assert cliente.post("/ui/eventos/no-existe/cancelar", data={}).status_code == 422
    assert cliente.post("/ui/eventos/no-existe/restaurar", data={}).status_code == 422


def test_cancelacion_y_restauracion(cliente):
    ev = alta(cliente, titulo="Fisio", inicio="2026-10-30T08:30")
    alta(cliente, titulo="Gimnasio", inicio="2026-10-30T19:00")
    r = cliente.post(f"/ui/eventos/{ev['id']}/cancelar", headers=HTMX,
                     data={"raiz": "", "volver": "semana:2026-10-26"})
    assert r.status_code == 200 and "Cancelado: &#39;Fisio&#39;" in r.text
    html = cliente.get("/ui").text
    assert "Fisio" not in vista(html) and "Gimnasio" in vista(html)
    assert "Fisio" in cancelados(html)
    assert "<details open>" in cancelados(r.text)        # tras cancelar, la lista se abre
    assert set(eventos_ics(cliente)) == {"Gimnasio"}

    r = cliente.post(f"/ui/eventos/{ev['id']}/restaurar", headers=HTMX,
                     data={"raiz": "", "volver": "semana:2026-10-26"})
    assert "Restaurado: &#39;Fisio&#39;" in r.text
    html = cliente.get("/ui").text
    assert "Fisio" in dia(html, "2026-10-30") and "Fisio" not in cancelados(html)
    assert set(eventos_ics(cliente)) == {"Fisio", "Gimnasio"}


def test_acciones_desde_la_vista_de_mes_vuelven_al_mes(cliente):
    ev = alta(cliente, titulo="Fisio", inicio="2026-11-20T08:30")
    r = cliente.post(f"/ui/eventos/{ev['id']}/cancelar", headers=HTMX, data={"raiz": "../", "volver": "mes:2026-11"})
    assert "Noviembre de 2026" in r.text and 'src="' not in r.text
    assert 'href="../ui/mes?mes=2026-12"' in r.text


def test_raiz_de_la_respuesta_parcial(cliente):
    datos = {"titulo": "x", "fecha": "2026-10-30", "volver": "semana:2026-10-26"}
    # La página que envía el formulario manda su raíz...
    r = cliente.post("/ui/eventos", headers=HTMX, data={**datos, "raiz": "../"})
    assert 'hx-post="../ui/eventos"' in r.text
    # ...y si no es un prefijo relativo válido, se ignora y se calcula desde la ruta.
    r = cliente.post("/ui/eventos", headers=HTMX, data={**datos, "raiz": "https://malicioso.com/"})
    assert "malicioso" not in r.text and 'hx-post="../ui/eventos"' in r.text
    # Sin htmx se devuelve la página completa con la raíz de la ruta de la acción.
    r = cliente.post("/ui/eventos", data={**datos, "raiz": ""})
    assert "<html" in r.text and 'src="../ui/static/htmx.min.js"' in r.text


def test_bloque_suscribirse(cliente):
    html = cliente.get("/ui").text
    assert 'data-ruta="calendario.ics"' in html and 'href="calendario.ics"' in html
    assert "iPhone" in html and "Añadir calendario suscrito" in html
    assert "Android" in html and "ICSx⁵" in html
    assert 'data-ruta="../calendario.ics"' in cliente.get("/ui/mes").text


def test_eventos_creados_por_el_asistente_se_distinguen(cliente, servicio):
    servicio.crear("Llamar al banco", "manana", "12:00")
    html = cliente.get("/ui").text
    assert "vía chat" in dia(html, "2026-10-30")


def test_titulos_se_escapan(cliente):
    alta(cliente, titulo="<script>alert(1)</script>", inicio="2026-10-30T08:30")
    html = cliente.get("/ui").text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html


# ── DEV-17 · README y compose de producción ───────────────────────────────

SECCIONES_README = [
    "## Capacidades", "## Frontend", "## Datos", "## Rutas HTTP", "## Variables de entorno",
    "## Arrancar solo", "## Guion manual del Incremento 1",
]


def test_readme_tiene_las_secciones_obligatorias():
    texto = (MODULO / "README.md").read_text(encoding="utf-8")
    titulos = [l.strip() for l in texto.splitlines() if l.startswith("## ")]
    for s in SECCIONES_README:
        assert any(t.startswith(s) for t in titulos), f"falta la sección {s}"
    for herramienta in ("crear_evento", "listar_eventos", "cancelar_evento", "buscar_huecos"):
        assert f"`{herramienta}" in texto
    assert "sqlite3" in texto and "calendario.ics" in texto


def _compose() -> dict | None:
    try:
        import yaml
    except ImportError:
        return None
    ruta = REPO / "docker-compose.yml"
    return yaml.safe_load(ruta.read_text(encoding="utf-8")) if ruta.exists() else None


_COMPOSE = _compose()


def _entorno(servicio: dict) -> dict[str, str]:
    env = servicio.get("environment") or {}
    if isinstance(env, list):
        env = dict(e.split("=", 1) for e in env)
    return {k: str(v) for k, v in env.items()}


@pytest.mark.skipif(_COMPOSE is None or "calendario" not in (_COMPOSE.get("services") or {}),
                    reason="docker-compose.yml aún no tiene el servicio calendario (o falta PyYAML)")
def test_compose_de_produccion():
    servicios = _COMPOSE["services"]
    cal = servicios["calendario"]
    assert "ports" not in cal, "los módulos no publican puertos"
    build = cal["build"] if isinstance(cal["build"], str) else cal["build"]["context"]
    assert build.rstrip("/") == "./modules/calendario"
    assert _entorno(cal)["ALLOWED_HOSTS"] == "calendario:*"
    assert "${DATA_LOCAL:-./data}/calendario:/data" in cal.get("volumes", [])
    core = servicios["core"]
    modulos = [m.strip() for m in _entorno(core)["MODULOS"].split(",")]
    assert "http://calendario:8001" in modulos and "http://prueba:8001" in modulos
    depende = core.get("depends_on", [])
    assert "calendario" in (depende if isinstance(depende, list) else list(depende))
    assert "CALENDARIO_PERMITIR_RESET" not in _entorno(cal)


@pytest.mark.xfail(reason="El Dockerfile (Lote A) aún no copia templates/: falta 'COPY templates ./templates'",
                   strict=False)
def test_dockerfile_copia_las_plantillas():
    texto = (MODULO / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^COPY\s+templates\s+\./templates\s*$", texto, re.M)

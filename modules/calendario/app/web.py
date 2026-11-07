"""Frontend del módulo Calendario (RF-11, Lote B: DEV-15 y DEV-16).

Vistas de semana (/ui) y de mes (/ui/mes), alta, edición, cancelación y
restauración manuales, y el bloque "Suscribirse" con la URL del .ics.
Jinja2 + htmx servido por el propio módulo (sin CDN, DA-04).

Reglas de este fichero:

- Solo enlaces RELATIVOS. El usuario llega a través del proxy del core
  (/m/calendario/ui), así que ninguna URL del HTML puede empezar por "/".
  Cada plantilla recibe `raiz`, el prefijo relativo que lleva desde la página
  actual a la raíz del módulo: "" en /ui, "../" en /ui/mes, etc.
- Las acciones (formularios) funcionan con htmx y sin él. Con htmx
  (cabecera HX-Request) se devuelve solo el contenido de #vista; la página
  que envía el formulario manda su propia `raiz` en un campo oculto, porque
  la ruta de la acción (/ui/eventos/<id>) tiene otra profundidad. Sin htmx
  se devuelve la página completa.
- Toda la lógica está en dominio.Calendario: aquí solo se traducen
  formularios a llamadas (alta_directa, editar, cancelar_por_id, restaurar,
  listar_rango) y resultados a vistas.

Se conserva la firma `registrar_rutas(mcp, servicio)`, a la que llama main.py.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, Response

from . import fechas
from .dominio import DIAS, MESES, Calendario, ErrorValidacion, NoEncontrado, a_bool

BASE = Path(__file__).resolve().parent
PLANTILLAS = BASE.parent / "templates"
ESTATICOS = BASE / "static"
# Lista cerrada: la ruta de estáticos no sirve nada que no esté aquí.
ESTATICOS_PERMITIDOS = {"htmx.min.js": "text/javascript; charset=utf-8"}

DIAS_CORTOS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MAX_EN_CELDA = 3          # eventos visibles por día en la vista de mes
MAX_CANCELADOS = 20       # los más recientes
_RAIZ_VALIDA = re.compile(r"^(?:\.\./){0,5}$")
_MES = re.compile(r"^(\d{4})-(\d{2})$")

entorno = Environment(
    loader=FileSystemLoader(str(PLANTILLAS)),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


# ── Utilidades ────────────────────────────────────────────────────────────

def raiz_desde_ruta(ruta: str) -> str:
    """Prefijo relativo a la raíz del módulo: '/ui' → '', '/ui/mes' → '../'."""
    return "../" * max(ruta.count("/") - 1, 0)


def _hhmm(dt: datetime) -> str:
    return f"{dt:%H:%M}"


def _fecha_larga(d: date, con_anio: bool = False) -> str:
    texto = f"{d.day} de {MESES[d.month - 1]}"
    return f"{texto} de {d.year}" if con_anio else texto


def _titulo_semana(lunes: date) -> str:
    domingo = lunes + timedelta(days=6)
    if lunes.year != domingo.year:
        return f"{_fecha_larga(lunes, True)} – {_fecha_larga(domingo, True)}"
    if lunes.month != domingo.month:
        return f"{_fecha_larga(lunes)} – {_fecha_larga(domingo)} de {domingo.year}"
    return f"{lunes.day} – {_fecha_larga(domingo)} de {domingo.year}"


def _describir(servicio: Calendario, ev: dict[str, Any]) -> str:
    """"'Fisio' el jueves 5 de noviembre a las 8:30"."""
    ini = servicio._inicio(ev)
    texto = f"'{ev['titulo']}' el {DIAS[ini.weekday()]} {_fecha_larga(ini.date())}"
    if not ev["todo_el_dia"]:
        texto += f" a las {ini.hour}:{ini.minute:02d}"
    return texto


def _aviso_solapes(servicio: Calendario, solapes: list[dict[str, Any]]) -> str:
    if not solapes:
        return ""
    partes = [f"'{o['titulo']}' ({_hhmm(servicio._inicio(o))}–{_hhmm(servicio._fin(o))})" for o in solapes]
    lista = partes[0] if len(partes) == 1 else ", ".join(partes[:-1]) + " y " + partes[-1]
    return f" Ojo: se solapa con {lista}."


# ── Modelo de vista ───────────────────────────────────────────────────────

def _vm_evento(servicio: Calendario, ev: dict[str, Any], dia: date | None = None) -> dict[str, Any]:
    """Datos de un evento listos para la plantilla (y para rellenar su formulario)."""
    ini, fin = servicio._inicio(ev), servicio._fin(ev)
    if ev["todo_el_dia"]:
        ultimo = (fin - timedelta(days=1)).date() if fin.date() > ini.date() else ini.date()
        cuando = "Todo el día"
        if ultimo != ini.date():
            cuando += f" (hasta el {DIAS[ultimo.weekday()]} {ultimo.day})"
        hora = hora_fin = ""
    else:
        if dia is not None and ini.date() < dia:
            cuando = f"hasta las {_hhmm(fin)}" if fin.date() == dia else "Todo el día"
        else:
            cuando = f"{_hhmm(ini)}–{_hhmm(fin)}"
            if fin.date() > ini.date():
                cuando += " (+1)" if fin.date() == ini.date() + timedelta(days=1) else f" ({fin:%d/%m})"
        hora, hora_fin = _hhmm(ini), _hhmm(fin)
    return {
        "id": ev["id"],
        "titulo": ev["titulo"],
        "ubicacion": ev.get("ubicacion") or "",
        "notas": ev.get("notas") or "",
        "origen": ev.get("origen") or "",
        "todo_el_dia": ev["todo_el_dia"],
        "cuando": cuando,
        "corto": "" if ev["todo_el_dia"] else _hhmm(ini),
        "fecha": ini.date().isoformat(),
        "hora": hora,
        "hora_fin": hora_fin,
        "continua": dia is not None and ini.date() < dia,
        "descripcion": _describir(servicio, ev),
    }


def _por_dia(servicio: Calendario, dias: list[date]) -> dict[date, list[dict[str, Any]]]:
    """Reparte los eventos activos del rango entre los días que ocupan (fin exclusivo)."""
    eventos = servicio.listar_rango(dias[0], dias[-1])
    reparto: dict[date, list[dict[str, Any]]] = {d: [] for d in dias}
    for ev in eventos:
        ini, fin = servicio._inicio(ev), servicio._fin(ev)
        for d in dias:
            desde = servicio._local(d)
            if ini < desde + timedelta(days=1) and fin > desde:
                reparto[d].append(_vm_evento(servicio, ev, d))
    for d in dias:  # día completo primero; luego por hora
        reparto[d].sort(key=lambda e: (not e["todo_el_dia"] and not e["continua"], e["hora"]))
    return reparto


def _cancelados(servicio: Calendario) -> list[dict[str, Any]]:
    todos = [e for e in servicio.listar_rango(incluir_cancelados=True) if e["cancelado_en"]]
    todos.sort(key=lambda e: e["cancelado_en"], reverse=True)
    return [_vm_evento(servicio, e) for e in todos[:MAX_CANCELADOS]]


def _contexto_comun(servicio: Calendario, raiz: str, vista: str, ref: date,
                    aviso: dict[str, str] | None, alta: dict[str, Any] | None) -> dict[str, Any]:
    hoy = servicio.ahora().date()
    return {
        "raiz": raiz,
        "vista": vista,
        "hoy": hoy.isoformat(),
        "aviso": aviso,
        "alta": alta or {"fecha": ref.isoformat()},
        "alta_abierta": bool(alta),
        "cancelados": _cancelados(servicio),
    }


def contexto_semana(servicio: Calendario, ref: date, raiz: str = "",
                    aviso: dict[str, str] | None = None, alta: dict[str, Any] | None = None) -> dict[str, Any]:
    hoy = servicio.ahora().date()
    lunes = ref - timedelta(days=ref.weekday())
    dias = [lunes + timedelta(days=i) for i in range(7)]
    reparto = _por_dia(servicio, dias)
    ctx = _contexto_comun(servicio, raiz, "semana", ref if ref in dias else lunes, aviso, alta)
    if hoy in dias and not alta:
        ctx["alta"] = {"fecha": hoy.isoformat()}
    ctx.update({
        "titulo": _titulo_semana(lunes),
        "volver": f"semana:{lunes.isoformat()}",
        "anterior": f"ui?semana={(lunes - timedelta(days=7)).isoformat()}",
        "siguiente": f"ui?semana={(lunes + timedelta(days=7)).isoformat()}",
        "actual": "ui",
        "es_actual": hoy in dias,
        "otra_vista": f"ui/mes?mes={ref:%Y-%m}",
        "dias": [{
            "fecha": d.isoformat(),
            "nombre": DIAS[d.weekday()].capitalize(),
            "numero": d.day,
            "mes": MESES[d.month - 1],
            "es_hoy": d == hoy,
            "pasado": d < hoy,
            "eventos": reparto[d],
        } for d in dias],
        "total": len({e["id"] for d in dias for e in reparto[d]}),
    })
    return ctx


def contexto_mes(servicio: Calendario, anio: int, mes: int, raiz: str = "",
                 aviso: dict[str, str] | None = None, alta: dict[str, Any] | None = None) -> dict[str, Any]:
    hoy = servicio.ahora().date()
    primero = date(anio, mes, 1)
    siguiente = date(anio + (mes == 12), mes % 12 + 1, 1)
    ultimo = siguiente - timedelta(days=1)
    anterior = (primero - timedelta(days=1)).replace(day=1)
    inicio = primero - timedelta(days=primero.weekday())
    fin = ultimo + timedelta(days=6 - ultimo.weekday())
    dias = [inicio + timedelta(days=i) for i in range((fin - inicio).days + 1)]
    reparto = _por_dia(servicio, dias)
    celdas = [{
        "fecha": d.isoformat(),
        "numero": d.day,
        "del_mes": d.month == mes,
        "es_hoy": d == hoy,
        "pasado": d < hoy,
        "eventos": reparto[d][:MAX_EN_CELDA],
        "mas": max(len(reparto[d]) - MAX_EN_CELDA, 0),
        "n": len(reparto[d]),
        "semana": (d - timedelta(days=d.weekday())).isoformat(),
    } for d in dias]
    ref = hoy if (hoy.year, hoy.month) == (anio, mes) else primero
    ctx = _contexto_comun(servicio, raiz, "mes", ref, aviso, alta)
    ctx.update({
        "titulo": f"{MESES[mes - 1].capitalize()} de {anio}",
        "volver": f"mes:{primero:%Y-%m}",
        "anterior": f"ui/mes?mes={anterior:%Y-%m}",
        "siguiente": f"ui/mes?mes={siguiente:%Y-%m}",
        "actual": "ui/mes",
        "es_actual": (hoy.year, hoy.month) == (anio, mes),
        "otra_vista": f"ui?semana={ref.isoformat()}",
        "cabeceras": DIAS_CORTOS,
        "semanas": [celdas[i:i + 7] for i in range(0, len(celdas), 7)],
        "total": len({e["id"] for d in dias if d.month == mes for e in reparto[d]}),
    })
    return ctx


# ── Parámetros y formularios ──────────────────────────────────────────────

def _fecha_o_none(valor: str | None) -> date | None:
    try:
        return date.fromisoformat((valor or "").strip()[:10])
    except ValueError:
        return None


def _mes_o_none(valor: str | None) -> tuple[int, int] | None:
    m = _MES.match((valor or "").strip())
    if m and 1 <= int(m.group(2)) <= 12:
        return int(m.group(1)), int(m.group(2))
    return None


def _evento_desde_formulario(f: dict[str, str]) -> dict[str, Any]:
    """Campos del formulario → argumentos de alta_directa / editar.

    Sin hora (o con "Todo el día" marcado) el evento ocupa el día completo,
    igual que en la herramienta crear_evento. Si la hora de fin es anterior a
    la de inicio, el evento termina al día siguiente ("de 23:00 a 01:00").
    """
    titulo = f.get("titulo", "").strip()
    if not titulo:
        raise ErrorValidacion("Falta el título")
    d = _fecha_o_none(f.get("fecha"))
    if d is None:
        raise ErrorValidacion("Falta la fecha o no es válida")
    hora, hora_fin = f.get("hora", "").strip(), f.get("hora_fin", "").strip()
    todo = a_bool(f.get("todo_el_dia", "")) or not hora  # "false", "0", "off" → no (QA A-04)
    datos: dict[str, Any] = {"titulo": titulo, "todo_el_dia": todo,
                             "ubicacion": f.get("ubicacion", "").strip(),
                             "notas": f.get("notas", "").strip()}
    if todo:
        datos.update(inicio=d.isoformat(), fin=None)
        return datos
    h = fechas.resolver_hora(hora)
    if h is None:
        raise ErrorValidacion(f"No entiendo la hora '{hora}'")
    ini = datetime.combine(d, h)
    fin = None
    if hora_fin:
        hf = fechas.resolver_hora(hora_fin)
        if hf is None:
            raise ErrorValidacion(f"No entiendo la hora de fin '{hora_fin}'")
        fin_dt = datetime.combine(d, hf)
        if fin_dt <= ini:
            fin_dt += timedelta(days=1)
        fin = fin_dt.isoformat(timespec="minutes")
    datos.update(inicio=ini.isoformat(timespec="minutes"), fin=fin)
    return datos


# ── Rutas ─────────────────────────────────────────────────────────────────

def registrar_rutas(mcp, servicio: Calendario) -> None:

    def pintar(request: Request, ctx: dict[str, Any], estado: int = 200) -> HTMLResponse:
        parcial = request.headers.get("hx-request") == "true"
        plantilla = entorno.get_template("_vista.html" if parcial else "pagina.html")
        return HTMLResponse(plantilla.render(**ctx), status_code=estado)

    def vista(volver: str, raiz: str, aviso=None, alta=None, ref: date | None = None) -> dict[str, Any]:
        """Contexto de la vista desde la que se envió un formulario ('semana:…' o 'mes:…')."""
        tipo, _, valor = (volver or "").partition(":")
        hoy = servicio.ahora().date()
        if tipo == "mes":
            am = (ref.year, ref.month) if ref else (_mes_o_none(valor) or (hoy.year, hoy.month))
            return contexto_mes(servicio, *am, raiz=raiz, aviso=aviso, alta=alta)
        return contexto_semana(servicio, ref or _fecha_o_none(valor) or hoy, raiz=raiz, aviso=aviso, alta=alta)

    async def formulario(request: Request) -> tuple[dict[str, str], str]:
        f = {k: str(v) for k, v in (await request.form()).items()}
        raiz = f.get("raiz", "")
        if request.headers.get("hx-request") != "true" or not _RAIZ_VALIDA.match(raiz):
            raiz = raiz_desde_ruta(request.url.path)
        return f, raiz

    def error(request: Request, f: dict[str, str], raiz: str, mensaje: str, alta=None) -> HTMLResponse:
        # Con htmx se responde 200 para que el aviso se muestre (htmx no
        # intercambia respuestas 4xx); sin htmx, 422.
        estado = 200 if request.headers.get("hx-request") == "true" else 422
        return pintar(request, vista(f.get("volver", ""), raiz,
                                     aviso={"tipo": "error", "texto": mensaje}, alta=alta), estado)

    @mcp.custom_route("/ui", methods=["GET"])
    async def ui_semana(request: Request) -> HTMLResponse:
        hoy = servicio.ahora().date()
        valor = request.query_params.get("semana")
        ref = _fecha_o_none(valor) or hoy
        aviso = None if not valor or _fecha_o_none(valor) else {
            "tipo": "error", "texto": f"Fecha no válida '{valor}'; te enseño la semana actual."}
        return pintar(request, contexto_semana(servicio, ref, raiz_desde_ruta(request.url.path), aviso))

    @mcp.custom_route("/ui/mes", methods=["GET"])
    async def ui_mes(request: Request) -> HTMLResponse:
        hoy = servicio.ahora().date()
        valor = request.query_params.get("mes")
        am = _mes_o_none(valor) or (hoy.year, hoy.month)
        aviso = None if not valor or _mes_o_none(valor) else {
            "tipo": "error", "texto": f"Mes no válido '{valor}'; te enseño el mes actual."}
        return pintar(request, contexto_mes(servicio, *am, raiz=raiz_desde_ruta(request.url.path), aviso=aviso))

    @mcp.custom_route("/ui/static/{nombre}", methods=["GET"])
    async def ui_estatico(request: Request) -> Response:
        nombre = request.path_params["nombre"]
        if nombre not in ESTATICOS_PERMITIDOS:
            return Response("No encontrado", status_code=404, media_type="text/plain")
        return FileResponse(ESTATICOS / nombre, media_type=ESTATICOS_PERMITIDOS[nombre],
                            headers={"Cache-Control": "public, max-age=86400"})

    @mcp.custom_route("/ui/eventos", methods=["POST"])
    async def ui_alta(request: Request) -> HTMLResponse:
        f, raiz = await formulario(request)
        try:
            r = servicio.alta_directa(origen="usuario", **_evento_desde_formulario(f))
        except ErrorValidacion as e:
            return error(request, f, raiz, f"No he podido añadirlo: {e}.", alta=f)
        ev = r["evento"]
        texto = f"Añadido: {_describir(servicio, ev)}." + _aviso_solapes(servicio, r["solapamientos"])
        return pintar(request, vista(f.get("volver", ""), raiz, ref=servicio._inicio(ev).date(),
                                     aviso={"tipo": "atencion" if r["solapamientos"] else "ok", "texto": texto}))

    @mcp.custom_route("/ui/eventos/{id}", methods=["POST"])
    async def ui_editar(request: Request) -> HTMLResponse:
        f, raiz = await formulario(request)
        id = request.path_params["id"]
        try:
            actual = servicio.obtener(id)
            campos = _evento_desde_formulario(f)
            if campos["todo_el_dia"] and actual["todo_el_dia"]:
                # Un evento de varios días completos conserva su duración.
                dias = (servicio._fin(actual).date() - servicio._inicio(actual).date()).days
                if dias > 1:
                    campos["fin"] = (date.fromisoformat(campos["inicio"]) + timedelta(days=dias)).isoformat()
            r = servicio.editar(id, **campos)
        except NoEncontrado:
            return error(request, f, raiz, "Ese evento ya no existe.")
        except ErrorValidacion as e:
            return error(request, f, raiz, f"No he podido guardar los cambios: {e}.")
        ev = r["evento"]
        texto = f"Guardado: {_describir(servicio, ev)}." + _aviso_solapes(servicio, r["solapamientos"])
        return pintar(request, vista(f.get("volver", ""), raiz, ref=servicio._inicio(ev).date(),
                                     aviso={"tipo": "atencion" if r["solapamientos"] else "ok", "texto": texto}))

    @mcp.custom_route("/ui/eventos/{id}/cancelar", methods=["POST"])
    async def ui_cancelar(request: Request) -> HTMLResponse:
        f, raiz = await formulario(request)
        try:
            ev = servicio.cancelar_por_id(request.path_params["id"])
        except NoEncontrado:
            return error(request, f, raiz, "Ese evento ya no existe.")
        texto = f"Cancelado: {_describir(servicio, ev)}. Puedes restaurarlo en «Cancelados»."
        ctx = vista(f.get("volver", ""), raiz, aviso={"tipo": "ok", "texto": texto})
        ctx["abrir_cancelados"] = True
        return pintar(request, ctx)

    @mcp.custom_route("/ui/eventos/{id}/restaurar", methods=["POST"])
    async def ui_restaurar(request: Request) -> HTMLResponse:
        f, raiz = await formulario(request)
        try:
            ev = servicio.restaurar(request.path_params["id"])
        except NoEncontrado:
            return error(request, f, raiz, "Ese evento ya no existe.")
        texto = f"Restaurado: {_describir(servicio, ev)}." + _aviso_solapes(servicio, servicio._solapamientos(ev))
        return pintar(request, vista(f.get("volver", ""), raiz, aviso={"tipo": "ok", "texto": texto}))

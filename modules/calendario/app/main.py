"""Módulo Calendario de Mortimer (Incremento 1).

Cumple el contrato de módulo (DA-05) igual que modules/prueba: un servidor
MCP con sus herramientas, rutas HTTP propias en el mismo puerto (8001),
/health y un frontend en /ui. Además publica el calendario como .ics
suscribible (RF-06) y una API REST que usan el frontend y el ejecutor del
banco de evaluación.

Herramientas (esquema v2, DA-08): el modelo extrae expresiones ("el viernes",
"17:00") y el módulo calcula las fechas. Todas devuelven
{"texto", "final", "datos"}; con final=True el core puede mostrar el texto sin
otra ronda del LLM.

Variables de entorno:
  DATA_DIR                   carpeta de datos (por defecto /data)
  ZONA_HORARIA               zona del usuario (Europe/Madrid)
  ALLOWED_HOSTS              hosts aceptados (localhost:*,127.0.0.1:*,calendario:*)
  CALENDARIO_PERMITIR_RESET  "1" habilita POST /api/reset (solo staging/evaluación)
  MORTIMER_AHORA             instante fijo ISO (p. ej. 2026-10-29T18:00), solo pruebas
  HUECOS_DESDE / HUECOS_HASTA  franja de buscar_huecos (08:00-21:00)
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Callable, TypedDict
from zoneinfo import ZoneInfo

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent
from pydantic import Field, WithJsonSchema
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

from . import ics, web
from .db import BaseDatos
from .dominio import Calendario, ErrorValidacion, NoEncontrado, a_bool

NOMBRE = "calendario"
VERSION = "0.1.0"
HOSTS_POR_DEFECTO = f"localhost:*,127.0.0.1:*,{NOMBRE}:*"


# ── Configuración desde el entorno ────────────────────────────────────────

def reloj_desde_entorno(zona: ZoneInfo, valor: str | None = None) -> Callable[[], datetime]:
    """Hora real en la zona del usuario o, si existe MORTIMER_AHORA, ese instante fijo."""
    valor = os.environ.get("MORTIMER_AHORA", "") if valor is None else valor
    valor = valor.strip()
    if not valor:
        return lambda: datetime.now(zona)
    fijo = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    fijo = fijo.replace(tzinfo=zona) if fijo.tzinfo is None else fijo.astimezone(zona)
    return lambda: fijo


def servicio_desde_entorno() -> Calendario:
    zona = ZoneInfo(os.environ.get("ZONA_HORARIA", "Europe/Madrid"))
    datos = Path(os.environ.get("DATA_DIR", "/data"))
    # La base no se crea hasta la primera operación: importar este módulo
    # (por ejemplo, para listar sus herramientas en una prueba) no toca el disco.
    return Calendario(
        BaseDatos(datos / "calendario.db"), zona, reloj_desde_entorno(zona),
        huecos_desde=os.environ.get("HUECOS_DESDE", "08:00"),
        huecos_hasta=os.environ.get("HUECOS_HASTA", "21:00"),
    )


# ── Herramientas MCP ──────────────────────────────────────────────────────

class Resultado(TypedDict):
    texto: str
    final: bool
    datos: dict[str, Any]


def _llamada(servicio: Calendario, nombre: str, argumentos: dict[str, Any],
             funcion: Callable[[], dict[str, Any]]) -> CallToolResult:
    """Ejecuta una herramienta y la anota en el registro de actividad (canal 'chat')."""
    t = time.perf_counter()
    try:
        r = funcion()
    except Exception as e:
        servicio.anotar("chat", nombre, argumentos, f"{type(e).__name__}: {e}", "error",
                        ms=(time.perf_counter() - t) * 1000)
        raise
    estado, evento_id = Calendario.estado_de(r)
    servicio.anotar("chat", nombre, argumentos, r["texto"], estado, evento_id,
                    (time.perf_counter() - t) * 1000)
    return _a_mcp(r)


def _a_mcp(r: dict[str, Any]) -> CallToolResult:
    # Contenido estructurado {"texto","final","datos"} y, como texto, la frase
    # para el usuario: es lo que verá el LLM si el core hace otra ronda.
    return CallToolResult(content=[TextContent(type="text", text=r["texto"])], structured_content=r)


DIA = ("Día tal como lo dice el usuario, sin calcular la fecha. Una de estas palabras: "
       "hoy, manana, pasado manana, lunes, martes, miercoles, jueves, viernes, sabado, domingo; "
       "o una fecha explícita ('3 de noviembre', '2026-11-03').")
HORA = "Hora de inicio en formato 24 h HH:MM, p. ej. '17:00' para 'las cinco de la tarde'. Vacía si no se dice."

# Tipos tolerantes (QA A-06): el esquema que ve el modelo no cambia ("string" e
# "integer", contrato §3.2), pero se aceptan valores de otro tipo (un modelo
# pequeño manda "hora": 10 o "duracion_min": "una hora") y el dominio los
# interpreta o responde con final=True. Así la validación del SDK nunca deja
# una llamada sin respuesta final.
Texto = Annotated[str | int | float | None, WithJsonSchema({"type": "string"})]
Entero = Annotated[int | float | str | None, WithJsonSchema({"type": "integer"})]


def _texto(valor: Any) -> str:
    """None → ''; 10 → '10'; 10.5 → '10.5'."""
    if valor is None:
        return ""
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    return str(valor)


# Metadatos para el core (contrato de módulo, DA-15). El core los usa antes de
# llamar a la herramienta y no cambian lo que ve el modelo:
#   fechas:   argumentos que son una expresión de día o de periodo. Si el
#             mensaje del usuario contiene una sola expresión temporal, el core
#             la usa en lugar de la que haya escrito el modelo ("pasado mañana"
#             que llega como "manana", "smana" como "mes"); si no contiene
#             ninguna y el argumento es opcional, lo deja vacío (el modelo se
#             inventaba "sábado" o "único" al cancelar).
#   requiere: la herramienta solo se ejecuta si el mensaje (o el anterior del
#             usuario, para las respuestas del tipo "la del tinte") encaja con
#             alguna de estas expresiones regulares, sobre texto en minúsculas y
#             sin tildes.
#   excluye:  no se ejecuta si el mensaje encaja con alguna.
# Si el core rechaza una llamada, se lo dice al modelo y le quita la herramienta
# en la ronda siguiente.
_ACCIONES_FUERA = (r"\b(manda\w*|envia\w*|whatsapp|sms|telegram|correo|e-?mail|"
                   r"recomienda\w*|recomiendame)\b")
_MOVER = (r"\b(pasa|pasalo|pasala|mueve\w*|cambia\w*|retrasa\w*|adelanta\w*|aplaza\w*|"
          r"traslada\w*|renombra\w*|modifica\w*|edita\w*|corrige\w*)\b")
# DA-17 (F1.0): el core también usa `requiere` ANTES de llamar al modelo, para
# despertar el módulo solo cuando el mensaje tiene que ver con él ("hola" no
# debe llevar las herramientas del calendario). Por eso crear_evento y
# listar_eventos, que antes no lo declaraban, lo declaran ahora. Las
# expresiones pecan de amplias a propósito: si el módulo se despierta de más,
# el modelo todavía puede no llamar a nada; si se despierta de menos, se pierde
# la petición.
_NUM = r"(\d{1,2}|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce)"
_TEMPORAL = (r"\b(hoy|manana|pasado manana|ayer|lunes|martes|miercoles|jueves|viernes|sabado|domingo|"
             r"finde|fin de semana|semana|mes|enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
             r"septiembre|setiembre|octubre|noviembre|diciembre|mediodia|medianoche|"
             r"esta (tarde|noche)|\d{1,2}/\d{1,2}|\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}|\d{1,2} ?h|"
             rf"a las? {_NUM}|de {_NUM} a {_NUM})\b")
_AGENDA = (r"\b(agenda|calendario|eventos?|citas?|reunion\w*|cumple\w*|planes|plan|"
           r"recordatorios?|recuerdame|recuerda\w*)\b")
_ALTA = (r"\b(apunt\w*|anot\w*|anad\w*|agend\w*|reserv\w*|program\w*|crea|crear|creame|"
         r"tengo|quedo|quedamos|he quedado|me toca)\b")
_CONSULTA = (r"\b(que tengo|tengo algo|que hay|hay algo|que planes|cuando (es|tengo|era)|"
             r"a que hora|consulta\w*|mira\w*|dime|lista\w*|ensename|muestrame)\b")
META: dict[str, dict[str, Any]] = {
    "crear_evento": {"mortimer": {"fechas": {"dia": "dia"},
                                  "requiere": [_ALTA, _TEMPORAL, _AGENDA],
                                  "excluye": [_ACCIONES_FUERA, _MOVER + r".*\b(a|al|para)\b"]}},
    "listar_eventos": {"mortimer": {"fechas": {"periodo": "periodo"},
                                    "requiere": [_CONSULTA, _TEMPORAL, _AGENDA]}},
    "cancelar_evento": {"mortimer": {
        "fechas": {"dia": "dia"},
        "requiere": [r"\b(cancel\w*|anul\w*|borr\w*|elimin\w*|quit\w*|suprim\w*|desapunt\w*|"
                     r"ya no (voy|tengo|hay|puedo))\b"]}},
    "editar_evento": {"mortimer": {"requiere": [_MOVER]}},
    "buscar_huecos": {"mortimer": {
        "fechas": {"dia": "dia"},
        "requiere": [r"\b(libres?|huecos?|cabe|caben|cabria|rato|ratos|disponibles?|tiempo|"
                     r"cuando puedo|tengo .*horas? seguidas)\b"]}},
}


def crear_servidor(servicio: Calendario) -> MCPServer:
    mcp = MCPServer(
        NOMBRE,
        version=VERSION,
        instructions="Calendario personal: crea, consulta y cancela eventos y busca huecos libres.",
    )

    @mcp.tool(meta=META["crear_evento"])
    def crear_evento(
        titulo: Annotated[str, Field(description="Título breve del evento, p. ej. 'Dentista'.")],
        dia: Annotated[str, Field(description=DIA)],
        hora: Annotated[Texto, Field(description=HORA + " Sin hora, el evento ocupa el día completo.")] = "",
        hora_fin: Annotated[Texto, Field(description="Hora de fin HH:MM, solo si el usuario la dice.")] = "",
        ubicacion: Annotated[str, Field(description="Lugar, solo si el usuario lo dice.")] = "",
    ) -> Annotated[CallToolResult, Resultado]:
        """Crea un evento en el calendario. Úsala también cuando el usuario cuenta que tiene algo o que ha quedado ('tengo dentista el martes', 'quedo con Pau el sábado'). No la uses para mover un evento que ya existe (eso es editar_evento) ni para cosas que no son del calendario, como mandar mensajes o recomendar algo."""
        args = {"titulo": titulo, "dia": dia, "hora": hora, "hora_fin": hora_fin, "ubicacion": ubicacion}
        return _llamada(servicio, "crear_evento", args,
                        lambda: servicio.crear(titulo, dia, _texto(hora), _texto(hora_fin), ubicacion))

    @mcp.tool(meta=META["listar_eventos"])
    def listar_eventos(
        periodo: Annotated[str, Field(description=(
            "Periodo tal como lo dice el usuario: 'hoy', 'manana', 'pasado manana', 'esta semana', "
            "'la semana que viene', 'fin de semana', 'este mes', 'el mes que viene' "
            "o un día ('viernes', '3 de noviembre')."))],
    ) -> Annotated[CallToolResult, Resultado]:
        """Consulta la agenda del usuario en un periodo: qué tiene hoy, mañana, esta semana, etc."""
        return _llamada(servicio, "listar_eventos", {"periodo": periodo}, lambda: servicio.listar(periodo))

    @mcp.tool(meta=META["cancelar_evento"])
    def cancelar_evento(
        titulo: Annotated[str, Field(description="Palabra clave del título del evento, p. ej. 'dentista'.")] = "",
        dia: Annotated[str, Field(description=DIA + " Déjalo vacío si el usuario no dice el día: no lo deduzcas.")] = "",
        id: Annotated[str, Field(description="Identificador del evento, solo si aparece en una respuesta anterior.")] = "",
    ) -> Annotated[CallToolResult, Resultado]:
        """Cancela un evento del calendario identificado por su título y, si se dice, su día. Si varios encajan, no cancela nada y pregunta cuál."""
        return _llamada(servicio, "cancelar_evento", {"titulo": titulo, "dia": dia, "id": id},
                        lambda: servicio.cancelar(titulo, dia, id))

    @mcp.tool(meta=META["editar_evento"])
    def editar_evento(
        titulo: Annotated[str, Field(description="Palabra clave del título del evento que ya existe, p. ej. 'yoga'.")],
        dia: Annotated[str, Field(description="Día en que está ahora el evento, solo si el usuario lo dice. "
                                              "Vacío si no lo dice.")] = "",
        nuevo_dia: Annotated[str, Field(description="Día al que se mueve, tal como lo dice el usuario "
                                                    "('jueves', '20 de noviembre'). Vacío si no cambia.")] = "",
        nueva_hora: Annotated[Texto, Field(description="Nueva hora de inicio HH:MM. Vacía si no cambia.")] = "",
        nueva_hora_fin: Annotated[Texto, Field(description="Nueva hora de fin HH:MM, solo si el usuario la dice.")] = "",
        nuevo_titulo: Annotated[str, Field(description="Nuevo nombre, solo si el usuario lo quiere cambiar.")] = "",
    ) -> Annotated[CallToolResult, Resultado]:
        """Mueve o cambia un evento que ya existe: otro día, otra hora u otro nombre ('pasa el yoga al jueves', 'retrasa el dentista a las 17:00'). Si varios encajan, no cambia nada y pregunta cuál."""
        args = {"titulo": titulo, "dia": dia, "nuevo_dia": nuevo_dia, "nueva_hora": nueva_hora,
                "nueva_hora_fin": nueva_hora_fin, "nuevo_titulo": nuevo_titulo}
        return _llamada(servicio, "editar_evento", args,
                        lambda: servicio.editar_por_chat(titulo, dia, nuevo_dia, _texto(nueva_hora),
                                                         _texto(nueva_hora_fin), nuevo_titulo))

    @mcp.tool(meta=META["buscar_huecos"])
    def buscar_huecos(
        dia: Annotated[str, Field(description=DIA)],
        duracion_min: Annotated[Entero, Field(description="Duración en minutos (una hora = 60).")],
    ) -> Annotated[CallToolResult, Resultado]:
        """Busca tiempo libre en un día para una duración dada. Solo cuando el usuario pregunta explícitamente si tiene tiempo libre o un hueco. Para apuntar un plan ('quedo con Pau el sábado') usa crear_evento."""
        return _llamada(servicio, "buscar_huecos", {"dia": dia, "duracion_min": duracion_min},
                        lambda: servicio.huecos(dia, duracion_min))

    _registrar_rutas(mcp, servicio)
    web.registrar_rutas(mcp, servicio)
    return mcp


# ── Rutas HTTP ────────────────────────────────────────────────────────────

def _error(mensaje: str, estado: int) -> JSONResponse:
    return JSONResponse({"error": mensaje}, status_code=estado)


class TipoNoAdmitido(Exception):
    """El cuerpo no es application/json (la API responde 415)."""


def _es_json(request: Request) -> bool:
    tipo = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    return tipo == "application/json"


def _sin_cuerpo_o_json(request: Request) -> None:
    """Para POST sin cuerpo (cancelar, restaurar): se admite sin Content-Type,
    pero un formulario o text/plain se rechaza (QA A-07)."""
    if request.headers.get("content-type") and not _es_json(request):
        raise TipoNoAdmitido()


async def _json(request: Request) -> dict[str, Any]:
    # Solo application/json (QA A-07): un formulario o un text/plain se pueden
    # enviar desde otra web sin preflight CORS (CSRF); con JSON, el navegador
    # tiene que pedir permiso antes y no se lo damos. Los formularios del
    # frontend van a sus propias rutas /ui/... (web.py).
    if not _es_json(request):
        raise TipoNoAdmitido()
    try:
        cuerpo = await request.json()
    except (json.JSONDecodeError, ValueError):
        raise ErrorValidacion("El cuerpo tiene que ser JSON") from None
    if not isinstance(cuerpo, dict):
        raise ErrorValidacion("El cuerpo tiene que ser un objeto JSON")
    return cuerpo


def _api(funcion):
    """Traduce las excepciones del dominio a respuestas HTTP."""
    async def envoltura(request: Request) -> Response:
        try:
            return await funcion(request)
        except ErrorValidacion as e:
            return _error(str(e), 400)
        except NoEncontrado:
            return _error("Evento no encontrado", 404)
        except TipoNoAdmitido:
            return _error("El cuerpo tiene que ser JSON (Content-Type: application/json)", 415)
    envoltura.__name__ = funcion.__name__
    return envoltura


def _registrar_rutas(mcp: MCPServer, servicio: Calendario) -> None:
    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"modulo": NOMBRE, "version": VERSION, "estado": "ok"})

    @mcp.custom_route("/calendario.ics", methods=["GET"])
    async def calendario_ics(_: Request) -> Response:
        cuerpo = ics.generar(servicio.listar_rango(), nombre="Mortimer",
                             ahora=servicio.ahora(), zona=str(servicio.zona))
        return Response(cuerpo, media_type="text/calendar; charset=utf-8",
                        headers={"Content-Disposition": 'inline; filename="calendario.ics"'})

    @mcp.custom_route("/api/eventos", methods=["GET", "POST"])
    @_api
    async def eventos(request: Request) -> Response:
        if request.method == "GET":
            q = request.query_params
            lista = servicio.listar_rango(q.get("desde"), q.get("hasta"),
                                          incluir_cancelados=a_bool(q.get("incluir_cancelados", "0")))
            return JSONResponse(lista)
        d = await _json(request)
        r = servicio.alta_directa(d.get("titulo", ""), d.get("inicio"), d.get("fin"),
                                  a_bool(d.get("todo_el_dia", False)), d.get("ubicacion") or "",
                                  d.get("notas") or "", origen="usuario")
        servicio.anotar("api", "alta", d, f"Añadido: '{r['evento']['titulo']}'", evento_id=r["evento"]["id"])
        return JSONResponse(r, status_code=201)

    @mcp.custom_route("/api/eventos/{id}", methods=["GET", "PATCH"])
    @_api
    async def evento(request: Request) -> Response:
        id = request.path_params["id"]
        if request.method == "GET":
            return JSONResponse(servicio.obtener(id))
        # editar(id, /, **campos): un "id" en el cuerpo acaba en 400 (QA A-05).
        cambios = await _json(request)
        r = servicio.editar(id, **cambios)
        servicio.anotar("api", "editar", cambios, f"Guardado: '{r['evento']['titulo']}'", evento_id=id)
        return JSONResponse(r)

    @mcp.custom_route("/api/eventos/{id}/cancelar", methods=["POST"])
    @_api
    async def cancelar(request: Request) -> Response:
        _sin_cuerpo_o_json(request)
        ev = servicio.cancelar_por_id(request.path_params["id"])
        servicio.anotar("api", "cancelar", {"id": ev["id"]}, f"Cancelado: '{ev['titulo']}'", evento_id=ev["id"])
        return JSONResponse({"evento": ev})

    @mcp.custom_route("/api/eventos/{id}/restaurar", methods=["POST"])
    @_api
    async def restaurar(request: Request) -> Response:
        _sin_cuerpo_o_json(request)
        ev = servicio.restaurar(request.path_params["id"])
        servicio.anotar("api", "restaurar", {"id": ev["id"]}, f"Restaurado: '{ev['titulo']}'", evento_id=ev["id"])
        return JSONResponse({"evento": ev})

    @mcp.custom_route("/api/reset", methods=["POST"])
    @_api
    async def reset(request: Request) -> Response:
        # Se lee en cada petición: solo staging y la evaluación lo activan.
        if os.environ.get("CALENDARIO_PERMITIR_RESET", "") != "1":
            return _error("Reset deshabilitado (CALENDARIO_PERMITIR_RESET != 1)", 403)
        d = await _json(request)
        n = servicio.reset(d.get("eventos", []))
        servicio.anotar("api", "reset", {"eventos": n}, f"Calendario reiniciado con {n} eventos")
        return JSONResponse({"eventos": n})


# ── Aplicación ASGI ───────────────────────────────────────────────────────

def _host_permitido(host: str | None, permitidos: list[str]) -> bool:
    """Mismas reglas que TransportSecuritySettings del SDK de MCP ('nombre:*' = cualquier puerto)."""
    if not host:
        return False
    if host in permitidos:
        return True
    return any(p.endswith(":*") and host.startswith(p[:-2] + ":") for p in permitidos)


class FiltroHost:
    """Protección contra DNS rebinding también para las rutas propias.

    El SDK de MCP solo comprueba el Host en /mcp; aquí se aplica la misma
    regla a /api, /ui y /calendario.ics, que también leen o cambian datos.
    """

    def __init__(self, app, permitidos: list[str]):
        self.app = app
        self.permitidos = permitidos

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            if not _host_permitido(Headers(scope=scope).get("host"), self.permitidos):
                await PlainTextResponse("Invalid Host header", status_code=421)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def crear_app(mcp: MCPServer, hosts: str | None = None):
    hosts = os.environ.get("ALLOWED_HOSTS", HOSTS_POR_DEFECTO) if hosts is None else hosts
    permitidos = [h.strip() for h in hosts.split(",") if h.strip()]
    app = mcp.streamable_http_app(
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=permitidos,
        ),
    )
    app.add_middleware(FiltroHost, permitidos=permitidos)
    return app


servicio = servicio_desde_entorno()
mcp = crear_servidor(servicio)
app = crear_app(mcp)

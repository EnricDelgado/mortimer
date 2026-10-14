"""Banco de frases del spike de modelos (fase 2, capítulo 3).

Fecha de referencia fija: lunes 12/10/2026, 10:00, Europe/Madrid. Así las
fechas relativas ("mañana", "el jueves") tienen una respuesta correcta única.

Cada caso fija la herramienta esperada (None = no debe llamar a ninguna) y
unas comprobaciones sobre los argumentos. Las comprobaciones son tolerantes
con el formato (aceptan "2026-10-13T09:00", "2026-10-13 09:00:00+02:00"...)
pero no con el valor.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

REFERENCIA = datetime(2026, 10, 12, 10, 0, tzinfo=ZoneInfo("Europe/Madrid"))

# Herramientas tal y como las expondrá el módulo Calendario (arquitectura §4).
HERRAMIENTAS = [
    {"type": "function", "function": {
        "name": "crear_evento",
        "description": "Crea un evento en el calendario del usuario.",
        "parameters": {"type": "object", "properties": {
            "titulo": {"type": "string", "description": "Título breve del evento"},
            "inicio": {"type": "string", "description": "Fecha y hora de inicio en ISO 8601, p. ej. 2026-10-13T09:00"},
            "fin": {"type": "string", "description": "Fecha y hora de fin en ISO 8601 (opcional)"},
            "ubicacion": {"type": "string", "description": "Lugar (opcional)"},
            "notas": {"type": "string", "description": "Notas (opcional)"},
        }, "required": ["titulo", "inicio"]},
    }},
    {"type": "function", "function": {
        "name": "listar_eventos",
        "description": "Lista los eventos del calendario entre dos fechas. Úsala para consultar la agenda.",
        "parameters": {"type": "object", "properties": {
            "desde": {"type": "string", "description": "Inicio del rango, ISO 8601"},
            "hasta": {"type": "string", "description": "Fin del rango, ISO 8601"},
        }, "required": ["desde", "hasta"]},
    }},
    {"type": "function", "function": {
        "name": "cancelar_evento",
        "description": "Cancela un evento. Se puede identificar por id o por título y fecha aproximada.",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "string", "description": "Id del evento si se conoce"},
            "titulo": {"type": "string", "description": "Título o descripción del evento"},
            "fecha": {"type": "string", "description": "Fecha aproximada del evento, ISO 8601"},
        }},
    }},
    {"type": "function", "function": {
        "name": "buscar_huecos",
        "description": "Busca franjas libres en un día para una duración dada.",
        "parameters": {"type": "object", "properties": {
            "fecha": {"type": "string", "description": "Día en ISO 8601, p. ej. 2026-10-14"},
            "duracion_min": {"type": "integer", "description": "Duración en minutos"},
        }, "required": ["fecha", "duracion_min"]},
    }},
]

# Comprobaciones: (argumento, tipo, valor esperado)
#   fecha   → la parte YYYY-MM-DD debe estar en el conjunto
#   hora    → la parte HH:MM debe coincidir
#   contiene→ el texto (sin mayúsculas) contiene la subcadena
#   igual   → valor numérico exacto
CASOS = [
    # Alta de eventos
    ("A01", "Tengo médico el martes a las nueve", "crear_evento",
     [("inicio", "fecha", {"2026-10-13"}), ("inicio", "hora", "09:00")]),
    ("A02", "Apúntame una reunión con Laura mañana a las cinco de la tarde", "crear_evento",
     [("inicio", "fecha", {"2026-10-13"}), ("inicio", "hora", "17:00")]),
    ("A03", "Cena con mis padres el sábado a las 21:30 en casa", "crear_evento",
     [("inicio", "fecha", {"2026-10-17"}), ("inicio", "hora", "21:30"), ("ubicacion", "contiene", "casa")]),
    ("A04", "Pon una llamada con el director el jueves de 10 a 11", "crear_evento",
     [("inicio", "fecha", {"2026-10-15"}), ("inicio", "hora", "10:00"), ("fin", "hora", "11:00")]),
    ("A05", "El 3 de noviembre tengo el dentista a las 16:15", "crear_evento",
     [("inicio", "fecha", {"2026-11-03"}), ("inicio", "hora", "16:15")]),
    ("A06", "Recuérdame que entrego el TFM el 11 de noviembre", "crear_evento",
     [("inicio", "fecha", {"2026-11-11"})]),
    ("A07", "Pasado mañana tengo pádel a las 7 de la tarde", "crear_evento",
     [("inicio", "fecha", {"2026-10-14"}), ("inicio", "hora", "19:00")]),
    # Consulta de agenda
    ("C01", "¿Qué tengo esta semana?", "listar_eventos",
     [("desde", "fecha", {"2026-10-12"}), ("hasta", "fecha", {"2026-10-18", "2026-10-19"})]),
    ("C02", "¿Qué tengo mañana?", "listar_eventos",
     [("desde", "fecha", {"2026-10-13"})]),
    ("C03", "Resúmeme el día de hoy", "listar_eventos",
     [("desde", "fecha", {"2026-10-12"})]),
    ("C04", "¿Tengo algo el viernes?", "listar_eventos",
     [("desde", "fecha", {"2026-10-16"})]),
    ("C05", "¿Qué planes tengo la semana que viene?", "listar_eventos",
     [("desde", "fecha", {"2026-10-19"}), ("hasta", "fecha", {"2026-10-25", "2026-10-26"})]),
    # Cancelación
    ("X01", "Cancela la entrevista del jueves", "cancelar_evento",
     [("titulo", "contiene", "entrevista"), ("fecha", "fecha", {"2026-10-15"})]),
    ("X02", "Anula la cena del sábado", "cancelar_evento",
     [("titulo", "contiene", "cena"), ("fecha", "fecha", {"2026-10-17"})]),
    ("X03", "Borra la reunión de mañana con Laura", "cancelar_evento",
     [("titulo", "contiene", "reuni"), ("fecha", "fecha", {"2026-10-13"})]),
    # Huecos
    ("H01", "¿Cuándo tengo una hora libre el miércoles?", "buscar_huecos",
     [("fecha", "fecha", {"2026-10-14"}), ("duracion_min", "igual", 60)]),
    ("H02", "Búscame media hora libre mañana para llamar al banco", "buscar_huecos",
     [("fecha", "fecha", {"2026-10-13"}), ("duracion_min", "igual", 30)]),
    # Fuera de alcance: no debe llamar a ninguna herramienta
    ("N01", "Cuéntame un chiste corto", None, []),
    ("N02", "¿Qué es el capitalismo de vigilancia?", None, []),
    ("N03", "Gracias, eso es todo", None, []),
]

# Resultado simulado que devolvería cada herramienta. Sirve para medir la
# segunda ronda (el modelo redacta la respuesta final a partir del resultado),
# que también cuenta para el tiempo total que percibe el usuario (RNF-03).
RESULTADOS_SIMULADOS = {
    "crear_evento": '{"id": "evt-1", "solapamiento": null}',
    "listar_eventos": '[{"titulo": "Reunión con Laura", "inicio": "2026-10-13T17:00"}, '
                      '{"titulo": "Pádel", "inicio": "2026-10-14T19:00"}]',
    "cancelar_evento": '{"cancelado": true, "titulo": "Entrevista", "inicio": "2026-10-15T16:00"}',
    "buscar_huecos": '[{"inicio": "10:00", "fin": "12:00"}, {"inicio": "16:00", "fin": "18:30"}]',
}


# ── Esquema v2 (DA-08): el modelo extrae expresiones, el código calcula fechas ──
_DIA = ("Día tal como lo dice el usuario, sin calcular la fecha: 'hoy', 'mañana', "
        "'pasado mañana', un día de la semana ('jueves') o una fecha explícita ('3 de noviembre').")
_HORA = "Hora en formato 24 h HH:MM, p. ej. '17:00' para 'las cinco de la tarde'."

HERRAMIENTAS_V2 = [
    {"type": "function", "function": {
        "name": "crear_evento",
        "description": "Crea un evento en el calendario del usuario.",
        "parameters": {"type": "object", "properties": {
            "titulo": {"type": "string", "description": "Título breve del evento"},
            "dia": {"type": "string", "description": _DIA},
            "hora": {"type": "string", "description": _HORA + " Vacío si no se dice."},
            "hora_fin": {"type": "string", "description": "Hora de fin HH:MM, solo si se dice."},
            "ubicacion": {"type": "string", "description": "Lugar, solo si se dice."},
        }, "required": ["titulo", "dia"]},
    }},
    {"type": "function", "function": {
        "name": "listar_eventos",
        "description": "Consulta la agenda del usuario en un periodo.",
        "parameters": {"type": "object", "properties": {
            "periodo": {"type": "string", "description":
                "Periodo tal como lo dice el usuario: 'hoy', 'mañana', 'esta semana', "
                "'la semana que viene', 'fin de semana', 'este mes' o un día ('viernes')."},
        }, "required": ["periodo"]},
    }},
    {"type": "function", "function": {
        "name": "cancelar_evento",
        "description": "Cancela un evento identificado por su título y su día.",
        "parameters": {"type": "object", "properties": {
            "titulo": {"type": "string", "description": "Palabra clave del evento, p. ej. 'dentista'"},
            "dia": {"type": "string", "description": _DIA},
        }, "required": ["titulo"]},
    }},
    {"type": "function", "function": {
        "name": "buscar_huecos",
        "description": "Busca franjas libres en un día para una duración dada.",
        "parameters": {"type": "object", "properties": {
            "dia": {"type": "string", "description": _DIA},
            "duracion_min": {"type": "integer", "description": "Duración en minutos (una hora = 60)"},
        }, "required": ["dia", "duracion_min"]},
    }},
]


def a_canonico(nombre: str, args: dict, hoy) -> dict:
    """Traduce argumentos v2 al formato v1 (ISO) con el resolvedor del módulo
    Calendario, para puntuarlos con las mismas comprobaciones."""
    import importlib.util
    from pathlib import Path
    ruta = Path(__file__).resolve().parents[1] / "modules" / "calendario" / "app" / "fechas.py"
    spec = importlib.util.spec_from_file_location("fechas", ruta)
    f = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(f)

    out = dict(args)
    iso = lambda x: x.isoformat(timespec="minutes") if x else None
    if nombre == "crear_evento":
        ini = f.resolver_momento(args.get("dia"), args.get("hora"), hoy)
        out["inicio"] = iso(ini)
        if ini and args.get("hora_fin"):
            hf = f.resolver_hora(args["hora_fin"])
            out["fin"] = iso(ini.replace(hour=hf.hour, minute=hf.minute)) if hf else None
    elif nombre == "listar_eventos":
        p = f.resolver_periodo(args.get("periodo"), hoy)
        if p:
            out["desde"], out["hasta"] = p[0].isoformat(), p[1].isoformat()
    elif nombre in ("cancelar_evento", "buscar_huecos"):
        d = f.resolver_dia(args.get("dia"), hoy)
        out["fecha"] = d.isoformat() if d else None
    return {k: v for k, v in out.items() if v is not None}

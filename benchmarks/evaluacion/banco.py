"""Banco de comandos de la evaluación final (fase 4, dimensión "corrección funcional").

BORRADOR v0 · 14/10/2026 · escrito ANTES de medir nada con él.

Por qué un banco distinto del del spike (benchmarks/casos.py): aquellas 20
frases se usaron para diseñar el esquema v2 y lo harán también con los
arreglos del Incremento 1. Medir el resultado final con ellas inflaría la cifra
(sobreajuste; ver benchmarks/resultados/20261014-1927/analisis.md, "Amenazas a
la validez"). Ninguna frase de aquí repite una del spike.

Reglas para que el banco siga siendo válido:
  1. No se usa para diseñar ni ajustar descripciones de herramientas. Si
     durante el desarrollo se descubre un fallo, se arregla con frases propias,
     no con estas.
  2. Se congela al cerrar el Incremento 3 (capítulo 3, tabla 3.4). Hasta
     entonces solo se añaden casos (p. ej. Vitácora) o se adapta el formato de
     argumentos si cambia el esquema; nunca se cambia el resultado esperado
     para que pase.
  3. Cada cambio queda en el historial de Git, que es la prueba de que se
     escribió antes de medir.

Fecha de referencia: jueves 29/10/2026, 18:00, Europe/Madrid. Se elige a
propósito distinta de la del spike (lunes 12/10) y al final de mes:
  - "el jueves" dicho un jueves → 05/11 (regla de fechas.py: la siguiente aparición)
  - "la semana que viene" cruza a noviembre
  - mañana = vie 30/10 · pasado mañana = sáb 31/10 · lunes = 02/11

Formato de cada caso: (id, tipo, frase, esperado, nota)
  esperado: {herramienta_aceptable: [comprobaciones]}
            la clave None significa "no llamar a ninguna herramienta"
  comprobaciones: (argumento, tipo, valor), las mismas que benchmarks/casos.py,
            evaluadas sobre los argumentos ya resueltos a ISO (desde, hasta,
            inicio, fin, fecha) por el adaptador del esquema vigente
            (hoy: casos.a_canonico).

Calendario + núcleo: 30 casos. Vitácora se añadirá antes de medirla, si entra
en el alcance (punto de control del 28/10).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

REFERENCIA = datetime(2026, 10, 29, 18, 0, tzinfo=ZoneInfo("Europe/Madrid"))
VERSION = "v0-borrador-2026-10-14"

TIPOS = {
    "directa": "formulación estándar",
    "coloquial": "registro informal o forma indirecta ('tengo X', 'me ha surgido')",
    "sin_tildes": "escrita sin tildes, como en el móvil",
    "errata": "con una errata",
    "limite": "caso límite de calendario (fin de mes, mismo día de la semana)",
    "ambigua": "falta información: lo correcto es preguntar o desambiguar",
    "fuera_alcance": "ninguna herramienta sirve",
    "trampa": "contiene palabras de calendario pero no es una petición de calendario",
    "destructiva": "acción destructiva que no debe ejecutarse sin confirmación",
}

CASOS = [
    # ── Alta de eventos (RF-03) ──────────────────────────────────────────────
    ("E-A01", "coloquial", "Mañana a las 8 y media tengo fisio",
     {"crear_evento": [("inicio", "fecha", {"2026-10-30"}), ("inicio", "hora", "08:30")]},
     "'tengo X' no debe ir a buscar_huecos (fallo A05/A07 del spike, con otra frase)"),
    ("E-A02", "sin_tildes", "apuntame cena con Marta el sabado a las nueve y media de la noche",
     {"crear_evento": [("inicio", "fecha", {"2026-10-31"}), ("inicio", "hora", "21:30"),
                       ("titulo", "contiene", "cena")]}, ""),
    ("E-A03", "limite", "Añade al calendario la revisión del coche el lunes 2 de noviembre a las 10",
     {"crear_evento": [("inicio", "fecha", {"2026-11-02"}), ("inicio", "hora", "10:00")]},
     "día de la semana y fecha explícita a la vez; cruza de mes"),
    ("E-A04", "limite", "El jueves que viene tengo entrevista de 16:00 a 17:30 en la oficina",
     {"crear_evento": [("inicio", "fecha", {"2026-11-05"}), ("inicio", "hora", "16:00"),
                       ("fin", "hora", "17:30"), ("ubicacion", "contiene", "oficina")]},
     "'el jueves' dicho un jueves"),
    ("E-A05", "directa", "Hoy a las ocho de la tarde quedo con Pau para correr",
     {"crear_evento": [("inicio", "fecha", {"2026-10-29"}), ("inicio", "hora", "20:00")]}, ""),
    ("E-A06", "directa", "Pon el cumple de mi hermana el 4/11",
     {"crear_evento": [("inicio", "fecha", {"2026-11-04"}), ("titulo", "contiene", "cumple")]},
     "sin hora: evento de día completo"),
    ("E-A07", "coloquial", "Me ha surgido una reunión de equipo pasado mañana a mediodía",
     {"crear_evento": [("inicio", "fecha", {"2026-10-31"}), ("inicio", "hora", "12:00")]}, ""),
    ("E-A08", "errata", "crea evnto: llamada con el banco el miercoles a las 9",
     {"crear_evento": [("inicio", "fecha", {"2026-11-04"}), ("inicio", "hora", "09:00")]}, ""),

    # ── Consulta de agenda (RF-04) ───────────────────────────────────────────
    ("E-C01", "coloquial", "¿Cómo tengo el día de mañana?",
     {"listar_eventos": [("desde", "fecha", {"2026-10-30"}), ("hasta", "fecha", {"2026-10-30", "2026-10-31"})]}, ""),
    ("E-C02", "coloquial", "¿Qué tengo el finde?",
     {"listar_eventos": [("desde", "fecha", {"2026-10-31"}), ("hasta", "fecha", {"2026-11-01", "2026-11-02"})]},
     "'finde' no está en fechas.py a 14/10: debe fallar hasta que se añada"),
    ("E-C03", "directa", "Dime qué me queda hoy",
     {"listar_eventos": [("desde", "fecha", {"2026-10-29"})]}, "cuarta petición de ejemplo: resumir el día"),
    ("E-C04", "ambigua", "¿Estoy libre el lunes por la mañana?",
     {"listar_eventos": [("desde", "fecha", {"2026-11-02"})],
      "buscar_huecos": [("fecha", "fecha", {"2026-11-02"})]},
     "las dos herramientas son razonables"),
    ("E-C05", "limite", "Enséñame la agenda de la semana que viene",
     {"listar_eventos": [("desde", "fecha", {"2026-11-02"}), ("hasta", "fecha", {"2026-11-08", "2026-11-09"})]},
     "cruza de mes"),
    ("E-C06", "directa", "¿Tengo algo el 5 de noviembre?",
     {"listar_eventos": [("desde", "fecha", {"2026-11-05"})]}, ""),

    # ── Cancelación (RF-05) ──────────────────────────────────────────────────
    ("E-X01", "directa", "Cancela el fisio de mañana",
     {"cancelar_evento": [("titulo", "contiene", "fisio"), ("fecha", "fecha", {"2026-10-30"})]}, ""),
    ("E-X02", "coloquial", "Quita lo del dentista",
     {"cancelar_evento": [("titulo", "contiene", "dentista")]},
     "sin día: si hay más de un 'dentista', el módulo debe devolver candidatos y no cancelar"),
    ("E-X03", "sin_tildes", "elimina la reunion del sabado",
     {"cancelar_evento": [("titulo", "contiene", "reuni"), ("fecha", "fecha", {"2026-10-31"})]}, ""),
    ("E-X04", "coloquial", "Al final no voy a la cena del viernes, bórrala",
     {"cancelar_evento": [("titulo", "contiene", "cena"), ("fecha", "fecha", {"2026-10-30"})]}, ""),
    ("E-X05", "ambigua", "Cancela la reunión",
     {"cancelar_evento": [("titulo", "contiene", "reuni")]},
     "con dos reuniones en la agenda: verificación manual de que NO se cancela ninguna sin confirmar (RF-05)"),

    # ── Huecos libres ────────────────────────────────────────────────────────
    ("E-H01", "coloquial", "¿Tengo hora y media libre el martes para ir al gimnasio?",
     {"buscar_huecos": [("fecha", "fecha", {"2026-11-03"}), ("duracion_min", "igual", 90)]},
     "empieza por 'tengo' pero pide tiempo libre"),
    ("E-H02", "directa", "Búscame un hueco de 45 minutos mañana",
     {"buscar_huecos": [("fecha", "fecha", {"2026-10-30"}), ("duracion_min", "igual", 45)]}, ""),
    ("E-H03", "coloquial", "¿Cuándo puedo meter una reunión de dos horas el lunes?",
     {"buscar_huecos": [("fecha", "fecha", {"2026-11-02"}), ("duracion_min", "igual", 120)]},
     "menciona 'reunión' pero no pide crearla"),

    # ── Ambiguas ─────────────────────────────────────────────────────────────
    ("E-M01", "ambigua", "Apunta algo para el viernes",
     {None: [], "crear_evento": [("inicio", "fecha", {"2026-10-30"})]},
     "lo ideal es preguntar qué; crear con fecha correcta se acepta"),
    ("E-M02", "ambigua", "Ponme una cita con el médico",
     {None: []}, "sin día: debe preguntar cuándo"),
    ("E-M03", "ambigua", "Mueve la reunión del lunes al martes",
     {None: []},
     "no hay herramienta de editar en el Incremento 1: debe decirlo. NUNCA cancelar_evento. "
     "Si se añade editar_evento, se cambia el esperado antes de congelar"),

    # ── Fuera de alcance, trampas y conversación (RF-01) ─────────────────────
    ("E-N01", "trampa", "¿Qué tiempo hará mañana?", {None: []}, "'mañana' no implica calendario"),
    ("E-N02", "fuera_alcance", "Pon música relajante", {None: []}, "debe decir que no puede"),
    ("E-N03", "fuera_alcance", "Hola Mortimer, ¿qué tal estás?", {None: []}, ""),
    ("E-N04", "fuera_alcance", "Explícame qué es el protocolo MCP", {None: []},
     "pregunta abierta: mide la latencia y el recorte de la conversación libre (DA-09)"),
    ("E-S01", "destructiva", "Borra todos los eventos del calendario", {None: []},
     "no hay borrado masivo: no debe ejecutar ninguna cancelación"),
]

# Agenda con la que se precarga el módulo antes de ejecutar el banco, para que
# las cancelaciones y consultas tengan algo sobre lo que actuar. E-X02 y E-X05
# dependen de que haya DOS eventos que coincidan.
AGENDA_INICIAL = [
    {"titulo": "Fisio", "inicio": "2026-10-30T08:30", "fin": "2026-10-30T09:30"},
    {"titulo": "Cena con amigos", "inicio": "2026-10-30T21:00"},
    {"titulo": "Reunión de proyecto", "inicio": "2026-10-31T10:00", "fin": "2026-10-31T11:00"},
    {"titulo": "Reunión con el director", "inicio": "2026-11-02T12:00", "fin": "2026-11-02T13:00"},
    {"titulo": "Dentista", "inicio": "2026-11-03T16:00"},
    {"titulo": "Dentista (revisión)", "inicio": "2026-11-24T16:00"},
    {"titulo": "Entrega TFM", "inicio": "2026-11-11T00:00"},
]

assert len({c[0] for c in CASOS}) == len(CASOS), "ids repetidos"
assert all(c[1] in TIPOS for c in CASOS), "tipo desconocido"

# Módulo `calendario`

Calendario personal de Mortimer (Incremento 1). Guarda los eventos en SQLite, los publica como fichero `.ics` al que se puede suscribir el móvil (RF-06) y tiene su propia página web para verlos y editarlos a mano (RF-11). Cumple el contrato de módulo (DA-05): servidor MCP con sus herramientas, rutas HTTP en el mismo puerto, `/health` y frontend en `/ui`.

Las cuatro partes de un módulo:

| Parte | Dónde |
|---|---|
| Capacidades | Cuatro herramientas MCP en `POST /mcp` (ver [Capacidades](#capacidades)) |
| Frontend | `GET /ui` (semana) y `GET /ui/mes`, servidos tras el proxy del core en `/m/calendario/ui` |
| README | Este fichero |
| Datos | `DATA_DIR/calendario.db` (SQLite) y su copia de solo lectura `GET /calendario.ics` |

## Capacidades

El modelo extrae expresiones tal como las dice el usuario ("el viernes", "17:00") y el módulo calcula las fechas con `app/fechas.py` (esquema v2, DA-08). Todas las herramientas devuelven `{"texto", "final", "datos"}`: con `final: true` el core puede enseñar `texto` tal cual, sin otra ronda del LLM.

| Herramienta | Argumentos | Qué hace |
|---|---|---|
| `crear_evento` | `titulo`, `dia`, `hora=""`, `hora_fin=""`, `ubicacion=""` | Crea un evento. Sin hora, ocupa el día completo; sin hora de fin, dura una hora. Si se solapa con otro, lo crea y avisa |
| `listar_eventos` | `periodo` | Agenda de hoy, mañana, esta semana, la que viene, el fin de semana, el mes o un día concreto |
| `cancelar_evento` | `titulo=""`, `dia=""`, `id=""` | Busca por título (sin distinguir tildes ni mayúsculas). La coincidencia exacta gana. Si encaja uno, lo cancela (borrado lógico, se puede restaurar). Si encajan varios, **no cancela nada** y pregunta cuál (RF-05). Si el evento solo se parece (erratas, o lo dicho es más largo que el título: «cena de empresa» frente a «Cena»), **no cancela**: lo propone y pregunta |
| `buscar_huecos` | `dia`, `duracion_min` | Franjas libres de ese día entre `HUECOS_DESDE` y `HUECOS_HASTA` |

Ejemplos de frases (propias, no son las del banco de evaluación):

- "Apunta que el miércoles a las 18:30 tengo tutoría con Carmen" → `crear_evento(titulo="Tutoría con Carmen", dia="miercoles", hora="18:30")`
- "El sábado es el cumple de mi sobrino" → `crear_evento(titulo="Cumpleaños del sobrino", dia="sabado")`, de día completo
- "¿Qué tengo pasado mañana?" → `listar_eventos(periodo="pasado manana")`
- "Quita la tutoría" → `cancelar_evento(titulo="tutoría")`
- "¿Me queda hora y media libre el jueves?" → `buscar_huecos(dia="jueves", duracion_min=90)`

## Frontend

Página web del módulo, en castellano y pensada para el móvil. Se abre desde el chat (enlace del módulo en la cabecera) o directamente en `http://<servidor>:<puerto del core>/m/calendario/ui`.

- **Semana** (`/ui`, `/ui?semana=AAAA-MM-DD`): los siete días de lunes a domingo, en lista en el móvil y en columnas en pantallas anchas. Botones ‹ Hoy › para moverse.
- **Mes** (`/ui/mes`, `/ui/mes?mes=AAAA-MM`): rejilla del mes; en el móvil cada día muestra puntos y, al pulsarlo, abre su semana.
- **Alta**: botón «+ Nuevo evento» o el «+» de cada día. Sin hora, el evento es de día completo.
- **Edición y cancelación**: al pulsar un evento se despliega su formulario, con «Guardar cambios» y «Cancelar este evento».
- **Cancelados**: lista con botón «Restaurar» (cancelar nunca borra).
- **Ver el calendario en el móvil**: dirección del `.ics` para copiar e instrucciones para iPhone y Android.
- Los eventos creados desde el chat llevan la marca «vía chat».

Detalles técnicos: Jinja2 (`templates/`) y htmx servido por el propio módulo (`app/static/htmx.min.js`, sin CDN, DA-04). Todas las URL del HTML son **relativas**, porque el usuario llega a través del proxy del core (`/m/calendario/...`); cada plantilla recibe `raiz`, el prefijo que lleva a la raíz del módulo desde la página actual. Los formularios funcionan también sin JavaScript.

## Rutas HTTP

Todas en el puerto 8001. Solo aceptan peticiones con un `Host` de `ALLOWED_HOSTS` (protección contra *DNS rebinding*; si no, 421).

| Método y ruta | Uso |
|---|---|
| `POST /mcp` | Servidor MCP (streamable HTTP). El proxy del core no lo expone |
| `GET /health` | `{"modulo", "version", "estado"}` |
| `GET /calendario.ics` | Calendario completo en iCalendar (eventos no cancelados) |
| `GET /api/eventos?desde=AAAA-MM-DD&hasta=AAAA-MM-DD&incluir_cancelados=0` | Lista JSON |
| `POST /api/eventos` | Alta: `{titulo, inicio, fin?, todo_el_dia?, ubicacion?, notas?}` → `{evento, solapamientos}` |
| `GET /api/eventos/{id}` · `PATCH /api/eventos/{id}` | Consulta y edición |
| `POST /api/eventos/{id}/cancelar` · `POST /api/eventos/{id}/restaurar` | Borrado lógico y restauración |
| `POST /api/reset` | Vacía la base y carga `{"eventos": [...]}` en una sola transacción. Solo con `CALENDARIO_PERMITIR_RESET=1`; si no, 403. Ids repetidos → 400 |
| `GET /ui`, `GET /ui/mes` | Frontend |
| `POST /ui/eventos`, `POST /ui/eventos/{id}`, `POST /ui/eventos/{id}/cancelar`, `POST /ui/eventos/{id}/restaurar` | Formularios del frontend (devuelven HTML) |
| `GET /ui/static/htmx.min.js` | htmx |

Los `POST` y `PATCH` de `/api/...` exigen `Content-Type: application/json` (si no, 415: evita CSRF con formularios o `text/plain`); `cancelar` y `restaurar` también se aceptan sin cuerpo. `todo_el_dia` admite booleano o texto (`"true"`, `"1"`, `"on"`, `"sí"`; el resto es falso). Un `PATCH` con `id` en el cuerpo → 400.

Formato de un evento: `id` (UUID, también UID del `.ics`), `titulo`, `inicio` y `fin` (ISO 8601 con zona; en los de día completo, `fin` es exclusivo: el día siguiente a las 00:00), `todo_el_dia`, `ubicacion`, `notas`, `origen` (`usuario`, `asistente` o `importado`), `creado_en`, `cancelado_en`.

## Variables de entorno

| Variable | Por defecto | Uso |
|---|---|---|
| `DATA_DIR` | `/data` | Carpeta de la base de datos |
| `ZONA_HORARIA` | `Europe/Madrid` | Zona del usuario |
| `ALLOWED_HOSTS` | `localhost:*,127.0.0.1:*,calendario:*` | Hosts aceptados (`nombre:*` = cualquier puerto). En producción, `calendario:*` |
| `HUECOS_DESDE` / `HUECOS_HASTA` | `08:00` / `21:00` | Franja de `buscar_huecos` |
| `CALENDARIO_PERMITIR_RESET` | (vacía) | `1` habilita `POST /api/reset`. **Solo staging y evaluación** |
| `MORTIMER_AHORA` | (vacía) | Instante fijo ISO (p. ej. `2026-10-29T18:00`) en lugar de la hora real. Solo pruebas y evaluación |

## Datos

Todo está en un único fichero SQLite, `DATA_DIR/calendario.db` (modo WAL, así que junto a él pueden aparecer `calendario.db-wal` y `calendario.db-shm`). En el servidor, con el `docker-compose.yml` de producción, es `${DATA_LOCAL:-./data}/calendario/calendario.db`. Igual que la base del core, debe estar en disco local y no en una carpeta del NAS montada por red.

Una tabla, `eventos`, con una fila por evento. Cancelar no borra la fila: rellena `cancelado_en`.

Leer los datos sin Mortimer:

```bash
# Próximos eventos activos
sqlite3 -readonly data/calendario/calendario.db \
  "SELECT substr(inicio,1,16), titulo, ubicacion FROM eventos
   WHERE cancelado_en IS NULL AND inicio >= date('now') ORDER BY inicio LIMIT 20;"

# Copia de seguridad consistente aunque el módulo esté en marcha
sqlite3 data/calendario/calendario.db ".backup 'calendario-$(date +%F).db'"

# Exportar a CSV
sqlite3 -header -csv data/calendario/calendario.db "SELECT * FROM eventos;" > eventos.csv
```

También se puede descargar el calendario completo como iCalendar y abrirlo con cualquier aplicación de calendario:

```bash
curl -o mortimer.ics http://<servidor>:<puerto del core>/m/calendario/calendario.ics
```

El `.ics` se genera en cada petición a partir de SQLite: es una vista de solo lectura, y los cambios que se hagan en el móvil sobre un calendario suscrito no vuelven a Mortimer.

## Arrancar solo

```bash
cd modules/calendario
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
DATA_DIR=../../data/calendario .venv/bin/uvicorn app.main:app --port 8002
# Frontend: http://localhost:8002/ui
.venv/bin/pytest -q
```

Para que el core lo descubra en local: `MODULOS=http://localhost:8001,http://localhost:8002`. En Docker, el `docker-compose.yml` de la raíz lo levanta como servicio `calendario`, sin puertos publicados: solo el core habla con él.

## Guion manual del Incremento 1

Criterio de terminado del Incremento 1 (tabla 3.3): funcionan las cuatro peticiones de ejemplo, una aplicación externa se suscribe al `.ics` y el módulo tiene README. Se ejecuta desde el móvil, contra el chat del core (`http://<ip-servidor>:<puerto>`), con el calendario vacío o con datos de prueba. Anotar para cada paso: resultado (OK / fallo), herramienta y argumentos que aparecen en la traza, tiempo de respuesta y observaciones.

**Preparación.** En el frontend (`/m/calendario/ui`) crear a mano dos eventos que se llamen parecido en días distintos de esta semana o la siguiente, por ejemplo «Clase de inglés» el martes a las 19:00 y «Clase de inglés (repaso)» el jueves a las 19:00, y un tercero cualquiera para hoy.

| # | Paso | Resultado esperado |
|---|---|---|
| 1 | **Dar de alta un evento.** Escribir en el chat: «el viernes a las 10 me toca llevar el coche al taller» | Llamada a `crear_evento` con `dia="viernes"`, `hora="10:00"`. Respuesta «Hecho: 'Taller' el viernes … a las 10:00.» (o título equivalente). El evento aparece en `/m/calendario/ui` con la marca «vía chat» |
| 2 | **Consultar la semana.** «¿qué tengo esta semana?» | `listar_eventos(periodo="esta semana")`. Respuesta agrupada por días con horas y títulos, que coincide con la vista de semana |
| 3 | **Resumir el día.** «¿cómo tengo el día de hoy?» | `listar_eventos(periodo="hoy")`. Enumera el evento de hoy; si no hay nada, «No tienes nada el … (hoy).» |
| 4 | **Cancelar una cita con dos candidatos.** «cancela la clase de inglés» | `cancelar_evento(titulo="inglés")` o similar. **No cancela nada** y pregunta cuál, con día y hora de las dos. En el frontend siguen las dos activas. Responder «la del jueves»: cancela solo esa, que pasa a «Cancelados» |
| 5 | **Frontend.** Abrir `/m/calendario/ui` desde el enlace del chat | Semana actual, hoy destacado. Probar ‹ ›, «Hoy» y la vista de mes. Ningún enlace roto (todo cuelga de `/m/calendario/`) |
| 6 | **Editar a mano.** Pulsar el evento del taller, cambiar la hora a 11:30 y guardar | Aviso «Guardado: …». La vista muestra 11:30–12:30 |
| 7 | **Cancelar y restaurar a mano.** «Cancelar este evento» y después «Restaurar» en «Cancelados» | Desaparece de la semana y vuelve al restaurar |
| 8 | **Suscribir el calendario.** En «Ver el calendario en el móvil», copiar la dirección y seguir los pasos de iPhone (o ICSx⁵ en Android) | La app de calendario muestra «Mortimer» con los eventos de los pasos anteriores (el del taller a las 11:30). La actualización no es inmediata: forzarla desde la app si hace falta |
| 9 | **Comprobar que cancelar no borra.** `sqlite3 -readonly data/calendario/calendario.db "SELECT titulo, cancelado_en FROM eventos"` en el servidor | La clase del jueves está con `cancelado_en` relleno; nada se ha borrado |

Capturas para la memoria: vista de semana tras el paso 1, la pregunta del paso 4, la vista de mes y el calendario suscrito en el móvil.

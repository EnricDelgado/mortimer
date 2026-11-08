# Crear un módulo para Mortimer

Un módulo añade capacidades a Mortimer: un calendario, unas notas, un control de gastos. Esta guía explica qué tiene que cumplir un módulo para que el core lo encuentre y lo use bien, y cómo hacerlo paso a paso. Los ejemplos salen de los dos módulos del repositorio:

- `modules/prueba/`: el módulo más pequeño posible (una herramienta, sin datos). **Es la plantilla de partida.**
- `modules/calendario/`: un módulo completo, con base de datos, frontend, registro de actividad y pruebas. Sirve de referencia para todo lo demás.

## 1. Qué es un módulo (y qué no es)

Un módulo es un **servidor MCP** ([Model Context Protocol](https://modelcontextprotocol.io)) que corre en su propio contenedor. Publica herramientas con nombre, descripción y JSON Schema, y el core las ofrece al modelo de lenguaje.

Reglas que no se negocian:

1. **El módulo no tiene LLM.** No llama a Ollama ni a ningún modelo. Recibe argumentos ya extraídos, hace su trabajo con código normal y devuelve un resultado. El core es el único que habla con el modelo.
2. **El módulo no tiene chat propio.** Mortimer es un solo asistente. Si el frontend del módulo quiere que el usuario hable con Mortimer, incluye el chat flotante del core (sección 7).
3. **El módulo no conoce a los demás.** No llama a otros módulos ni sabe que existen. Solo el core conoce el mapa herramienta → módulo.
4. **El módulo es dueño de sus datos.** Los guarda en su carpeta `DATA_DIR`, en un formato que se pueda leer sin Mortimer (SQLite, `.ics`, markdown, CSV).
5. **El módulo no publica puertos.** Solo el core habla con él, por la red interna de Docker. El usuario llega a su frontend a través del proxy del core.

## 2. Cómo lo usa el core

```
usuario ─► core ─► modelo (Ollama): "¿qué herramienta uso?"
              │
              ├─► módulo A  POST /mcp   (tools/list al arrancar, tools/call en cada petición)
              └─► módulo B  POST /mcp
navegador ─► core /m/<modulo>/<ruta> ─► módulo  GET /<ruta>   (frontend y API REST)
```

1. Al arrancar, el core lee la variable `MODULOS` (lista de URL base separadas por comas) y se conecta a `<url>/mcp` de cada una (`core/app/registro.py`).
2. Pide `tools/list` y guarda cada herramienta con su descripción, su esquema y sus metadatos `meta.mortimer`.
3. El **nombre del módulo** es el nombre del servidor MCP (`MCPServer("calendario", ...)`). Es el que aparece en el mapa de módulos y en el proxy: `/m/calendario/ui`.
4. Los nombres de herramienta **no llevan prefijo**: el modelo ve `crear_evento`, no `calendario__crear_evento`. Si dos módulos publican el mismo nombre, el core se queda con el primero y descarta el segundo con un aviso en el log.
5. Si un módulo no responde al arrancar, el core sigue sin él y lo reintenta cada 15 segundos. También se puede forzar con `POST /api/modulos/recargar`.
6. En cada petición del usuario, el core **despierta** solo los módulos cuyas herramientas encajan con el mensaje, según su `requiere` y `excluye` (DA-17, sección 4.4). Si no encaja ninguna, el modelo contesta sin herramientas: es charla, o algo que Mortimer no sabe hacer.
7. El modelo ve la lista de módulos instalados con la frase de `instructions` de cada uno («Tus módulos instalados: …»). Con ella contesta a «¿qué sabes hacer?» y, si le piden algo que no cubre ningún módulo, responde «No tengo ningún módulo para eso». El core lo apunta en «Lo que aún no sé hacer», en la portada.
8. Con las herramientas despiertas, el modelo elige herramienta y argumentos. El core comprueba la llamada con los metadatos del módulo (sección 5), la ejecuta con `tools/call` y, si el módulo ya redactó la respuesta, se la enseña al usuario sin volver a llamar al modelo (sección 4).

## 3. Lista de comprobación del contrato

| | Requisito | Ejemplo en el calendario |
|---|---|---|
| Obligatorio | Servidor MCP con transporte *streamable HTTP* en `POST /mcp`, puerto 8001 | `app/main.py`, `mcp.streamable_http_app(...)` |
| Obligatorio | Nombre de servidor único, corto, en minúsculas y sin espacios | `MCPServer("calendario", version=..., instructions=...)` |
| Obligatorio | `instructions`: una frase con lo que sabe hacer el módulo. Mortimer la usa para presentarse (DA-17) | «Calendario personal: crea, consulta y cancela eventos y busca huecos libres.» |
| Obligatorio | `requiere` en `meta.mortimer` de **cada** herramienta: sin él, la herramienta se ofrece al modelo en todos los mensajes, también en un «hola» (DA-17) | `META` en `app/main.py` |
| Obligatorio | Herramientas con docstring y descripción de cada argumento | `crear_evento`, `listar_eventos`... |
| Obligatorio | `GET /health` → `{"modulo", "version", "estado": "ok"}` | `_registrar_rutas` |
| Obligatorio | Protección contra *DNS rebinding* con `ALLOWED_HOSTS` | `crear_app()` y `FiltroHost` |
| Obligatorio | Datos solo en `DATA_DIR` | `DATA_DIR/calendario.db` |
| Obligatorio | `README.md` del módulo: herramientas, frontend, rutas, variables y datos | `modules/calendario/README.md` |
| Obligatorio | `Dockerfile` y servicio en `docker-compose.yml` sin `ports:` | servicio `calendario` |
| Recomendado | Respuesta `{"texto", "final", "datos"}` con `final: true` | `_a_mcp()` |
| Recomendado | Resto de metadatos `meta.mortimer` (`fechas`, `excluye`) | `META` en `app/main.py` |
| Recomendado | Frontend en `GET /ui` con URLs relativas y el chat flotante del core | `app/web.py`, `templates/` |
| Recomendado | Registro de actividad propio (pestaña «Registro») | tabla `actividad` |
| Recomendado | Pruebas en memoria, sin Ollama | `tests/test_mcp.py` |

## 4. Paso a paso

### 4.1 Copiar la plantilla

```bash
cp -r modules/prueba modules/gastos
```

Dentro hay:

```
modules/gastos/
├── Dockerfile
├── README.md
├── requirements.txt      # mcp==2.3.0, uvicorn (versiones fijadas)
└── app/
    ├── __init__.py
    └── main.py           # servidor MCP, /health, /ui
```

Cambia `NOMBRE = "prueba"` por el nombre del módulo y rellena `instructions` con una frase que diga qué hace. El core la enseña en el mapa de módulos.

Usa las mismas versiones de `mcp` que el core (`core/requirements.txt`). La API 2.x del SDK no es compatible con la 1.x: se importa `MCPServer` desde `mcp.server`, no `FastMCP`.

### 4.2 Escribir las herramientas

El modelo que usa Mortimer es pequeño (3B parámetros, en CPU). Elige bien cuando las herramientas son pocas, claras y con argumentos sencillos. Pautas:

- **Pocas herramientas, con nombres de verbo en castellano**: `crear_gasto`, `listar_gastos`, `borrar_gasto`.
- **El docstring es la instrucción para el modelo.** Di cuándo usarla y, sobre todo, cuándo **no** usarla. Ejemplo del calendario: «No la uses para mover un evento que ya existe (eso es editar_evento) ni para cosas que no son del calendario».
- **Cada argumento con `Field(description=...)`** y un ejemplo.
- **Tipos simples**: `string` e `integer`. Nada de objetos anidados ni listas.
- **El modelo extrae, el código calcula** (decisión DA-08). Pide «el viernes» o «mañana» tal cual lo dice el usuario y calcula la fecha en el módulo. Los modelos pequeños se equivocan al calcular fechas ISO.
- **Argumentos opcionales con valor vacío por defecto**, y que la herramienta funcione o pregunte si faltan.
- **Si hay ambigüedad, no actúes: pregunta.** Si «cancela la clase» encaja con dos eventos, el calendario no cancela nada y devuelve la pregunta.

```python
from typing import Annotated
from pydantic import Field

@mcp.tool(meta=META["crear_gasto"])
def crear_gasto(
    concepto: Annotated[str, Field(description="Qué se ha pagado, p. ej. 'Supermercado'.")],
    importe: Annotated[str, Field(description="Importe tal como lo dice el usuario, p. ej. '23,50'.")],
    dia: Annotated[str, Field(description="Día tal como lo dice el usuario: hoy, ayer, lunes... Vacío si no lo dice.")] = "",
) -> Annotated[CallToolResult, Resultado]:
    """Apunta un gasto. Úsala cuando el usuario diga que ha pagado o gastado algo. No la uses para consultar gastos (eso es listar_gastos)."""
    return _a_mcp(servicio.crear(concepto, importe, dia))
```

Un modelo pequeño a veces manda `"importe": 23` en vez de `"23"`. Si el SDK rechaza la llamada por el tipo, el usuario se queda sin respuesta. El calendario acepta varios tipos y le enseña al modelo solo uno (`Texto` y `Entero` en `modules/calendario/app/main.py`):

```python
from pydantic import WithJsonSchema
Texto = Annotated[str | int | float | None, WithJsonSchema({"type": "string"})]
```

### 4.3 Devolver la respuesta

Cada herramienta devuelve dos cosas a la vez:

- **Contenido de texto**: la frase para el usuario. Es lo que verá el modelo si el core hace otra ronda.
- **Contenido estructurado** `{"texto", "final", "datos"}`:
  - `texto`: la misma frase.
  - `final`: `true` si la frase ya es la respuesta definitiva. El core la enseña tal cual y se ahorra otra llamada al modelo (varios segundos en CPU, y el modelo ya no puede reescribirla mal). Usa `false` solo si el resultado son datos que el modelo tiene que resumir.
  - `datos`: lo que haya hecho la herramienta (el evento creado, la lista...), para pruebas y para el registro.

```python
from typing import Any, TypedDict
from mcp.types import CallToolResult, TextContent

class Resultado(TypedDict):
    texto: str
    final: bool
    datos: dict[str, Any]

def _a_mcp(r: dict[str, Any]) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=r["texto"])], structured_content=r)
```

Escribe las frases en castellano, cortas y concretas: «Hecho: 'Dentista' el martes 10 de noviembre a las 17:00.» Si algo falla, explica qué falta («¿Qué día es?») en lugar de lanzar una excepción.

Los resultados de una herramienta son **datos**, no instrucciones. El prompt del core ya le dice al modelo que no obedezca órdenes que aparezcan dentro de ellos, pero no metas en `texto` contenido de terceros sin necesidad.

### 4.4 Declarar los metadatos `meta.mortimer`

El core usa lo que el módulo declara en `meta` dos veces: **antes** de llamar al modelo, para decidir qué herramientas se le ofrecen (DA-17), y **después**, para comprobar la llamada antes de ejecutarla (DA-15, `core/app/argumentos.py`). El modelo no ve estos metadatos, y el core no sabe nada del módulo: solo aplica lo declarado.

| Clave | Formato | Qué hace el core |
|---|---|---|
| `fechas` | `{"argumento": "dia" \| "periodo"}` | Si el mensaje del usuario contiene **una sola** expresión temporal, la usa en lugar de la que escribió el modelo. Si no contiene ninguna y el argumento es opcional, lo deja vacío. Declara como mucho un argumento |
| `requiere` | lista de expresiones regulares | La herramienta solo se ofrece al modelo, y solo se ejecuta, si el mensaje encaja con alguna. El mensaje anterior del usuario solo cuenta si Mortimer acaba de hacer una pregunta («¿Cuál de las dos?» → «la del tinte»). **Obligatorio desde DA-17** |
| `excluye` | lista de expresiones regulares | La herramienta no se ofrece ni se ejecuta si el mensaje actual encaja con alguna |

Las expresiones se aplican al mensaje **en minúsculas y sin tildes** («miércoles» llega como «miercoles»). Si el core rechaza una llamada, se lo dice al modelo y le quita esa herramienta en la ronda siguiente.

```python
META = {
    "crear_gasto": {"mortimer": {
        "fechas": {"dia": "dia"},
        "requiere": [r"\b(gast\w*|pag\w*|compr\w*|cuesta|costado|euros?)\b"],
    }},
    "borrar_gasto": {"mortimer": {
        "requiere": [r"\b(borr\w*|elimin\w*|quit\w*|anul\w*)\b"],
    }},
}
```

Úsalos con cuidado. Una expresión demasiado estricta deja sin herramienta frases que sí la necesitaban, y eso es peor que una demasiado amplia: si el módulo se despierta de más, el modelo todavía puede no llamar a nada. Prueba con frases reales de los dos lados: peticiones que deben despertar el módulo y charla que no («hola», «¿qué tal?», «gracias»). El Calendario tiene un ejemplo en `tests/test_qa_staging.py` (`test_la_charla_no_despierta_el_calendario`).

### 4.5 Añadir `/health`

```python
from starlette.requests import Request
from starlette.responses import JSONResponse

@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"modulo": NOMBRE, "version": VERSION, "estado": "ok"})
```

### 4.6 Proteger el módulo

Una web maliciosa abierta en el navegador del usuario podría intentar llamar al módulo con *DNS rebinding*. Por eso el módulo solo acepta peticiones cuyo `Host` esté en `ALLOWED_HOSTS` (decisión DA-05):

```python
_hosts = os.environ.get("ALLOWED_HOSTS", f"localhost:*,127.0.0.1:*,{NOMBRE}:*")
app = mcp.streamable_http_app(
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[h.strip() for h in _hosts.split(",") if h.strip()],
    ),
)
```

El SDK solo comprueba el `Host` en `/mcp`. Si el módulo tiene rutas propias que leen o cambian datos (`/api`, `/ui`), aplica la misma regla a todas con un middleware, como `FiltroHost` en `modules/calendario/app/main.py`.

Si el módulo tiene una API REST que cambia datos, exige `Content-Type: application/json` en `POST` y `PATCH` (responde 415 si no). Un formulario o un `text/plain` se pueden enviar desde otra web sin permiso del navegador; un JSON, no.

### 4.7 Frontend del módulo

Cada módulo puede tener su página en `GET /ui`. Sirve para **ver y gestionar los datos** del módulo a mano. No es otro asistente.

**URLs siempre relativas.** El usuario llega por el proxy del core (`http://<servidor>:8000/m/<modulo>/ui`), así que ninguna URL del HTML puede empezar por `/`. El calendario pasa a cada plantilla una variable `raiz` con el prefijo que lleva desde la página actual a la raíz del módulo:

```python
def raiz_desde_ruta(ruta: str) -> str:
    """'/ui' → '', '/ui/mes' → '../', '/ui/registro' → '../'"""
    return "../" * max(ruta.count("/") - 1, 0)
```

```html
<a href="{{ raiz }}ui/mes">Mes</a>
<script src="{{ raiz }}ui/static/htmx.min.js"></script>
```

**Sin CDN** (decisión DA-04). Si usas htmx u otra librería, sírvela desde el propio módulo. Mortimer tiene que funcionar sin internet.

**El chat es el del core.** Incluye el widget flotante con `data-core` apuntando a la raíz del core. Desde una página del módulo, la raíz del core es `raiz + "../../"` (se sale de `/m/<modulo>/`):

```html
<script src="{{ raiz }}../../static/chat-flotante.js" data-core="{{ raiz }}../../" defer></script>
```

La conversación es la misma en todas las páginas de Mortimer: el widget la guarda en el navegador y sigue al usuario al cambiar de página. Si el módulo se abre por su propio puerto, sin el core delante, el script no carga y la página funciona igual sin chat.

**Refrescar la página cuando Mortimer toca los datos.** El widget emite dos eventos en `document`:

| Evento | Cuándo | `detail` |
|---|---|---|
| `mortimer:respuesta` | Después de cada respuesta del chat | La respuesta de `POST /api/chat`, con `herramientas: [{modulo, herramienta, argumentos, ok, ...}]` |
| `mortimer:conversacion-borrada` | Al empezar una conversación nueva u olvidar la actual | `{conversacion}` |

```js
document.addEventListener('mortimer:respuesta', function (e) {
  var r = e.detail || {};
  var usado = (r.herramientas || []).some(function (h) { return h.modulo === 'gastos'; });
  if (usado) recargarVista();   // vuelve a pedir lo que se está viendo
});
```

El widget expone también `window.Mortimer` con `abrir()`, `cerrar()`, `enviar(texto)`, `conversacion()`, `olvidar()` y `continuar(id)`, por si la página quiere, por ejemplo, un botón «Pregúntale a Mortimer» que abra el chat con una frase.

### 4.8 Registro de actividad (recomendado)

El core ya guarda cada petición del usuario en su tabla `interacciones` (petición, herramientas, argumentos, tiempos). **Esa es la fuente de verdad** de lo que ha pasado en el chat y la que se usa en la evaluación.

Aun así, conviene que el módulo lleve su propio registro de lo que **ha ejecutado**, porque el core no ve los cambios hechos a mano en el frontend ni por la API. El calendario lo hace con una tabla `actividad` y una pestaña «Registro» en su frontend:

| Campo | Contenido |
|---|---|
| `fecha` | Hora real del cambio, ISO 8601 con zona |
| `canal` | `chat` (llamada a una herramienta desde Mortimer), `manual` (frontend) o `api` (REST) |
| `operacion` | `crear_evento`, `editar`, `cancelar`... |
| `argumentos` | JSON, tal como llegaron |
| `resultado` | Frase devuelta o aviso mostrado |
| `estado` | `ok` (hizo lo pedido), `pregunta` (no tocó nada y devolvió una pregunta) o `error` |
| `evento_id`, `ms` | Elemento afectado y duración |

Reglas que sigue el calendario y que sirven para cualquier módulo:

- **El módulo no guarda lo que dice el usuario.** Para enseñar las peticiones, la pestaña las lee del core con `GET ../../api/interacciones` desde el navegador y filtra las de su módulo.
- Las consultas no se anotan, solo lo que cambia datos o lo que devuelve una pregunta.
- Anotar **nunca** hace fallar la operación (`try/except` alrededor).
- «Vaciar el registro» borra el registro, nunca los datos.

### 4.9 Datos

- Todo en `DATA_DIR` (por defecto `/data` en el contenedor). El compose lo monta en `${DATA_LOCAL}/<modulo>`.
- SQLite **en disco local**, nunca en una carpeta de red (SMB/NFS): los bloqueos de SQLite no son fiables ahí.
- No crees la base al importar el módulo, sino en la primera operación. Así las pruebas que solo listan herramientas no tocan el disco.
- Borrar con cuidado: el calendario hace borrado lógico (`cancelado_en`) y deja restaurar.
- Explica en el README del módulo cómo leer los datos sin Mortimer (`sqlite3`, exportar a CSV, `.ics`...).

### 4.10 Dockerfile y compose

El `Dockerfile` de la plantilla vale tal cual. Si el módulo guarda datos, añade:

```dockerfile
ENV DATA_DIR=/data
RUN mkdir -p /data
VOLUME ["/data"]
```

Si usas `zoneinfo`, añade `tzdata` a `requirements.txt`: la imagen `python:3.12-slim` no trae la base de zonas horarias.

En `docker-compose.yml`, un servicio **sin `ports:`** y la URL del módulo en `MODULOS` del core:

```yaml
  core:
    environment:
      MODULOS: "http://prueba:8001,http://calendario:8001,http://gastos:8001"
    depends_on: [ollama, prueba, calendario, gastos]

  gastos:
    build: ./modules/gastos
    restart: unless-stopped
    environment:
      ALLOWED_HOSTS: "gastos:*"
      ZONA_HORARIA: Europe/Madrid
    volumes:
      - "${DATA_LOCAL:-./data}/gastos:/data"
```

El nombre del servicio en el compose, el de `ALLOWED_HOSTS` y el del servidor MCP deberían coincidir.

### 4.11 Probarlo

**Sin Docker**, en local, con Ollama en marcha:

```bash
cd modules/gastos
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
DATA_DIR=../../data/gastos .venv/bin/uvicorn app.main:app --port 8003
```

Y el core apuntando a él:

```bash
cd core
MODULOS=http://localhost:8001,http://localhost:8003 DATA_DIR=../data/core ../.venv/bin/uvicorn app.main:app --port 8000
```

En `http://localhost:8000` debe aparecer el módulo en el mapa, con sus herramientas, y su frontend en `http://localhost:8000/m/gastos/ui`.

**Pruebas automáticas, sin Ollama.** El cliente MCP del SDK se puede conectar a un servidor en memoria, sin red. Es como lo prueban el core y el calendario:

```python
from mcp import Client
from app.main import mcp

async def test_publica_sus_herramientas():
    async with Client(mcp) as c:
        assert c.server_info.name == "gastos"
        nombres = {t.name for t in (await c.list_tools()).tools}
        assert nombres == {"crear_gasto", "listar_gastos", "borrar_gasto"}

async def test_crear_gasto_da_respuesta_final():
    async with Client(mcp) as c:
        r = await c.call_tool("crear_gasto", {"concepto": "Pan", "importe": "1,20", "dia": "hoy"})
        assert not r.is_error
        assert r.structured_content["final"] is True
```

Comprueba también que una petición con un `Host` no permitido recibe 421 o 403.

**Con el modelo real**, escribe diez o veinte frases como las diría el usuario, incluidas algunas que **no** deberían usar el módulo («hola», «mándale un mensaje a Laura»), y mira en `/conversaciones` qué herramienta eligió el modelo y con qué argumentos.

## 5. Errores frecuentes

| Síntoma | Causa | Solución |
|---|---|---|
| El módulo sale como caído en el mapa | No arrancó, o la URL de `MODULOS` está mal | `docker compose logs <modulo>`; la URL es la base, sin `/mcp` |
| `421 Invalid Host header` | El `Host` con el que llama el core no está en `ALLOWED_HOSTS` | En Docker, `ALLOWED_HOSTS: "<servicio>:*"` |
| Una herramienta no aparece | Otro módulo ya publica ese nombre | Cambia el nombre; el log del core lo avisa |
| Enlaces rotos en el frontend | Alguna URL empieza por `/` | Usa siempre rutas relativas con `raiz` |
| El chat no aparece en la página del módulo | Ruta mal calculada a `chat-flotante.js` | `data-core` y `src` con `raiz + "../../"`; abre la página por `/m/<modulo>/ui` |
| El modelo llama a la herramienta cuando no toca | Docstring ambiguo | Di cuándo no usarla y añade `requiere` o `excluye` |
| El modelo calcula mal fechas o importes | Le pides el valor calculado | Pide la expresión del usuario y calcula en el módulo |
| Respuestas lentas tras usar la herramienta | `final` es `false` y el modelo redacta otra ronda | Devuelve la frase redactada con `final: true` |

# Mortimer

Asistente personal **local y modular**. El modelo de lenguaje corre en tu máquina, tus datos se guardan en formatos abiertos que puedes leer sin Mortimer, y las capacidades se amplían con módulos que hablan un protocolo público (MCP).

Este repositorio forma parte de un Trabajo Fin de Máster (TFM). Estado: **Incremento 0 cerrado**, Incremento 1 (módulo Calendario) en curso. El núcleo conversa y enruta peticiones a un módulo de prueba. Modelo por defecto: `granite4.1:3b` (DA-06).

## Dónde corre

Mortimer está pensado para un equipo **siempre encendido**. El equipo de referencia del TFM es un servidor doméstico: ThinkCentre M910q (i5-7500T, 8 GB de RAM, sin GPU) con Ubuntu Server y Docker, y un NAS Synology para almacenamiento. El Mac solo se usa para desarrollar.

## Servidor (despliegue y evaluación)

```bash
cp .env.example .env
docker compose up -d --build
docker compose exec ollama ollama pull granite4.1:3b
```

Guía completa de instalación y uso: ver `docs/` (en preparación).

## Mac (desarrollo)

Requiere [Ollama](https://ollama.com) nativo, que usa la GPU vía Metal, y Python 3.10 o superior.

```bash
ollama serve                 # en otra terminal
ollama pull granite4.1:3b
./scripts/dev.sh
```

Después abre <http://localhost:8000>, pulsa el botón **Mortimer** de abajo a la derecha y pregunta "¿qué hora es?". Debajo de la respuesta aparece la traza de la llamada: `prueba.dime_la_hora`.

## Estructura

| Ruta | Qué es |
|---|---|
| `core/` | Núcleo: chat web, bucle de *tool calling*, registro de módulos, log de interacciones |
| `modules/prueba/` | Módulo mínimo (una herramienta). Sirve de plantilla para módulos nuevos |
| `benchmarks/` | Comparativa de modelos locales en castellano |
| `docs/arquitectura.md` | Decisiones técnicas fechadas |

## Frontend del core

Mortimer es un solo asistente y se le habla siempre igual: desde el **chat flotante** (botón abajo a la derecha), que es el mismo en las páginas del core y en los frontends de los módulos. La conversación abierta sigue al usuario al cambiar de página.

| Página | Qué muestra |
|---|---|
| `/` | Mapa de módulos: estado, qué herramientas aporta cada uno, cuánto se ha usado y enlace a su frontend |
| `/conversaciones` | Registro de conversaciones. Se puede borrar una o todas |
| `/conversaciones/<id>` | Una conversación con las herramientas que invocó cada turno. "Continuar en el chat" la reabre |

Para incluir el chat en el frontend de un módulo (ruta relativa a la raíz del core, por el proxy `/m/<modulo>/`):

```html
<script src="../../static/chat-flotante.js" data-core="../../"></script>
```

Tras cada respuesta emite el evento `mortimer:respuesta` en `document` (detalle = respuesta de `/api/chat`), para que la página refresque sus datos. Expone `window.Mortimer` con `abrir()`, `cerrar()`, `enviar(texto)`, `conversacion()`, `olvidar()` y `continuar(id)`.

Borrar una conversación la elimina del historial que recibe el modelo **y** del registro de interacciones (contiene el texto de las peticiones). La base usa `secure_delete`, así que el texto no queda en el fichero.

## Endpoints del core

| Método | Ruta | |
|---|---|---|
| POST | `/api/chat` | `{"texto": "...", "conversacion": "opcional", "canal": "api" \| "web" \| "telegram"}` |
| GET | `/api/conversaciones` | Conversaciones: primera petición, número de turnos, fechas y canales |
| GET | `/api/conversaciones/<id>` | Turnos de una conversación con sus herramientas (404 si no existe) |
| DELETE | `/api/conversaciones/<id>` | Borra una conversación (historial y registro) |
| DELETE | `/api/conversaciones` | Borra todas |
| GET | `/api/modulos` | Módulos registrados y su estado |
| POST | `/api/modulos/recargar` | Volver a descubrir módulos sin reiniciar |
| GET | `/api/interacciones` | Registro de interacciones (datos de la evaluación) |
| GET, POST… | `/m/<modulo>/<ruta>` | Proxy al frontend y la API REST de cada módulo |
| GET | `/health` | Estado, modelo y herramientas disponibles |

## Configuración

Variables de entorno del core (en el servidor, en `.env`):

| Variable | Por defecto | Qué hace |
|---|---|---|
| `MODELO` | `granite4.1:3b` | Modelo de Ollama (DA-06) |
| `NUM_CTX` | `8192` | Ventana de contexto fija: evita recortes silenciosos y recargas del modelo |
| `NUM_PREDICT` | `160` | Tope de tokens por respuesta (DA-09). Si se alcanza, la respuesta se recorta en la última frase completa y la interacción queda marcada con `truncada=1`. `0` lo desactiva |
| `OLLAMA_THINK` | `false` | Desactiva el razonamiento en modelos *thinking* |
| `MAX_RONDAS` | `4` | Rondas máximas de herramientas por petición |
| `HISTORIAL` | `10` | Mensajes previos de la conversación que se envían al modelo |

## Pruebas

```bash
cd core && ../.venv/bin/pytest -q
```

Las pruebas no necesitan Ollama. El LLM se sustituye por un guion fijo y el módulo de prueba se conecta en memoria con el mismo protocolo.

## Benchmark de modelos

Se ejecuta en el servidor, contra el Ollama del compose:

```bash
python3 -m venv .venv && .venv/bin/pip install -r core/requirements.txt
docker compose exec ollama ollama pull granite4.1:3b
# Medición corta (~3 min por modelo): 1 repetición, solo la decisión
.venv/bin/python benchmarks/benchmark_modelos.py --modelos granite4.1:3b --repeticiones 1 --sin-ronda2 --num-ctx 4096
```

Son 20 frases en castellano (alta, consulta y cancelación de eventos, búsqueda de huecos y peticiones fuera de alcance), con 1 a 3 repeticiones por modelo. En el spike v1, con temperatura 0, las 3 repeticiones eligieron siempre la misma herramienta, así que para comparar esquemas basta con 1. El resultado se guarda en `benchmarks/resultados/<fecha>/resumen.md`, y los resultados se versionan en el repo como evidencia para la memoria.

Por defecto usa el esquema v2: el modelo extrae expresiones de fecha y `modules/calendario/app/fechas.py` las resuelve (DA-08). Con `--esquema v1` se repite la medición original, en la que el modelo calcula las fechas ISO.

**No uses el tag `qwen3:4b` a secas**: apunta a la variante *thinking*, que razona aunque se le pida `think=false`. Usa `qwen3:4b-instruct`.

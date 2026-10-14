# Mortimer

Asistente personal **local y modular**. El modelo de lenguaje corre en tu máquina, tus datos se guardan en formatos abiertos que puedes leer sin Mortimer, y las capacidades se amplían con módulos que hablan un protocolo público (MCP).

Este repositorio forma parte de un Trabajo Fin de Máster (TFM). Estado: **Incremento 0**. El núcleo conversa y enruta peticiones a un módulo de prueba.

## Dónde corre

Mortimer está pensado para un equipo **siempre encendido**. El equipo de referencia del TFM es un servidor doméstico: ThinkCentre M910q (i5-7500T, 8 GB de RAM, sin GPU) con Ubuntu Server y Docker, y un NAS Synology para almacenamiento. El Mac solo se usa para desarrollar.

## Servidor (despliegue y evaluación)

```bash
cp .env.example .env
docker compose up -d --build
docker compose exec ollama ollama pull qwen3:4b
```

## Mac (desarrollo)

Requiere [Ollama](https://ollama.com) nativo, que usa la GPU vía Metal, y Python 3.10 o superior.

```bash
ollama serve                 # en otra terminal
ollama pull qwen3:4b
./scripts/dev.sh
```

Después abre <http://localhost:8000> y pregunta "¿qué hora es?". Debajo de la respuesta aparece la traza de la llamada: `→ prueba.dime_la_hora(...)`.

## Estructura

| Ruta | Qué es |
|---|---|
| `core/` | Núcleo: chat web, bucle de *tool calling*, registro de módulos, log de interacciones |
| `modules/prueba/` | Módulo mínimo (una herramienta). Sirve de plantilla para módulos nuevos |
| `benchmarks/` | Comparativa de modelos locales en castellano |
| `docs/arquitectura.md` | Decisiones técnicas fechadas |

## Endpoints del core

| Método | Ruta | |
|---|---|---|
| GET | `/` | Chat web |
| POST | `/api/chat` | `{"texto": "...", "conversacion": "opcional"}` |
| GET | `/api/modulos` | Módulos registrados y su estado |
| POST | `/api/modulos/recargar` | Volver a descubrir módulos sin reiniciar |
| GET | `/api/interacciones` | Registro de interacciones (datos de la evaluación) |

## Pruebas

```bash
cd core && ../.venv/bin/pytest -q
```

Las pruebas no necesitan Ollama. El LLM se sustituye por un guion fijo y el módulo de prueba se conecta en memoria con el mismo protocolo.

## Benchmark de modelos

Se ejecuta en el servidor, contra el Ollama del compose:

```bash
python3 -m venv .venv && .venv/bin/pip install -r core/requirements.txt
for m in granite4.1:3b qwen3:4b-instruct qwen3:1.7b; do docker compose exec ollama ollama pull $m; done
.venv/bin/python benchmarks/benchmark_modelos.py --modelos granite4.1:3b,qwen3:4b-instruct,qwen3:1.7b
```

Son 20 frases en castellano (alta, consulta y cancelación de eventos, búsqueda de huecos y peticiones fuera de alcance), con 3 repeticiones por modelo. El resultado se guarda en `benchmarks/resultados/<fecha>/resumen.md`, y los resultados se versionan en el repo como evidencia para la memoria.

Por defecto usa el esquema v2: el modelo extrae expresiones de fecha y `modules/calendario/app/fechas.py` las resuelve (DA-08). Con `--esquema v1` se repite la medición original, en la que el modelo calcula las fechas ISO.

**No uses el tag `qwen3:4b` a secas**: apunta a la variante *thinking*, que razona aunque se le pida `think=false`. Usa `qwen3:4b-instruct`.

# Arquitectura y registro de decisiones

La arquitectura completa está en la memoria (capítulo 4). Este fichero guarda las **decisiones fechadas**: qué se decidió, frente a qué alternativas y por qué. Cada ficha sigue el formato del capítulo 3 (contexto, alternativas, decisión, consecuencias).

## Estado actual (Incremento 0)

```
                      Servidor doméstico (ThinkCentre M910q · Ubuntu Server · Docker)
                    ┌──────────────────────────────────────────────────────────────┐
navegador / móvil ──┼─► core :8000 ──► ollama :11434 (contenedor, CPU)             │
  (red de casa)     │      │                                                       │
                    │      └── MCP (streamable HTTP, red interna) ──► prueba :8001 │
                    │      SQLite en disco local                                   │
                    └──────────────┬───────────────────────────────────────────────┘
                                   └── NAS Synology: copias de seguridad y, más adelante, el vault de Vitácora
```

Desarrollo en el Mac (`scripts/dev.sh`, Ollama nativo); despliegue y medición en el servidor (`docker compose up -d`).

- `core/app/registro.py`: al arrancar se conecta por MCP a cada URL de `MODULOS`, pide `tools/list` y monta el catálogo de herramientas. Si un módulo no responde, el core arranca igualmente sin él.
- `core/app/orquestador.py`: bucle de *tool calling*. El LLM elige herramienta y argumentos, el core llama al módulo y el resultado vuelve al LLM. Hay un máximo de rondas; en la última ya no se ofrecen herramientas.
- `core/app/memoria.py`: SQLite con el historial y el **registro de interacciones** (petición, módulo, herramientas, tiempos). Es el origen de los datos de la evaluación.

---

## DA-01 · Contrato de módulo: MCP · 12/10/2026

**Contexto.** Hay que fijar cómo se anuncia y se invoca un módulo. El capítulo 3 menciona un "registro de módulos mediante manifiesto". La arquitectura proponía probar primero MCP con un límite de tiempo.

**Alternativas.**
- A. Cada módulo es un servidor MCP (Model Context Protocol, protocolo abierto).
- B. REST con un manifiesto propio (`GET /manifest` + un endpoint por herramienta).

**Decisión.** A. Se usa el SDK oficial `mcp` 2.3.0 con transporte *streamable HTTP*. El módulo publica sus herramientas con nombre, descripción y JSON Schema, y el core las descubre con `tools/list`. Con eso el "manifiesto" lo da el propio protocolo y no hace falta definir uno.

**Regla de corte.** Si el 19/10/2026 no hay un módulo respondiendo por MCP de extremo a extremo, se pasa a B. **Resultado (12/10):** el módulo de prueba responde por MCP desde el core por HTTP real, así que la regla no se activa.

**Consecuencias.**
- Un módulo de Mortimer sirve también fuera de Mortimer, con cualquier cliente MCP.
- Hay que alinear el texto del cap. 3: "manifiesto" pasa a ser "descubrimiento de herramientas mediante el protocolo MCP".
- Dependencia de un SDK que cambia rápido: la API 2.x no es compatible con la 1.x (`FastMCP` → `MCPServer`). Las versiones quedan fijadas en `requirements.txt`.

## DA-02 · Nombres de herramienta sin prefijo de módulo · 12/10/2026

**Decisión.** El LLM ve `crear_evento`, no `calendario__crear_evento`. Si dos módulos publican el mismo nombre, el registro se queda con el primero y anota un aviso.
**Motivo.** Los nombres naturales ayudan a los modelos pequeños a elegir bien. Las colisiones son improbables con dos módulos. **Revisar** si se añade un tercero o si el banco de pruebas muestra confusiones.

## DA-03 · API nativa de Ollama (`/api/chat`) · 12/10/2026

**Decisión.** Se usa la API nativa de Ollama y no su capa compatible con OpenAI.
**Motivo.** La nativa devuelve las duraciones de carga, de prompt y de generación, que van al registro y a la evaluación de rendimiento. Se usa temperatura 0 y `think=false` por defecto, porque los modelos con razonamiento tardan mucho más antes de llamar a una herramienta.

## DA-04 · Frontend sin dependencias externas en tiempo de ejecución · 12/10/2026

**Decisión.** htmx 2.0.4 se sirve desde `core/app/static/` y no desde un CDN.
**Motivo.** Con un CDN, el chat dejaría de funcionar sin internet e incumpliría el RNF-02, y el navegador haría una petición a un tercero en cada carga, lo que choca con el RNF-01.

## DA-05 · Protección contra DNS rebinding en los módulos · 12/10/2026

**Decisión.** Cada módulo solo acepta peticiones MCP cuyo `Host` sea `localhost`, `127.0.0.1` o su nombre de servicio en Docker (variable `ALLOWED_HOSTS`).
**Motivo.** Sin esa comprobación, una web maliciosa abierta en el navegador del usuario podría invocar herramientas de un módulo local. Es un caso de abuso del capítulo 4.

## DA-07 · Equipo de referencia: el servidor doméstico, no el Mac · 12/10/2026

**Contexto.** Un asistente que tiene que estar disponible a cualquier hora necesita un equipo encendido siempre. El Mac M3 es el equipo de desarrollo, pero no está siempre encendido ni dedicado. El autor ya tiene un servidor doméstico: un ThinkCentre M910q con Ubuntu Server 26.04 y Docker, más un NAS Synology para almacenamiento.

| Componente | Valor medido (12/10/2026) |
|---|---|
| CPU | Intel Core i5-7500T: 4 núcleos / 4 hilos, 2,7-3,3 GHz, AVX2, 35 W, GPU integrada HD 630 (Ollama no la usa) |
| RAM | 8 GB DDR4 en **un solo módulo (canal único)**; ranura B vacía. El módulo es de 2667 MT/s, pero la CPU admite hasta 2400 |
| Disponible para Mortimer | unos 5,6 GB (el resto lo usan el sistema y otros contenedores) |
| SO / contenedores | Ubuntu 26.04.1 LTS · Docker Compose v5.6.0 |

**Decisión.** El sistema se despliega y **se evalúa** en el ThinkCentre. Todas las cifras de rendimiento de la memoria (RNF-03, banco de pruebas) se toman ahí. El Mac solo se usa para desarrollar y, como mucho, como punto de comparación.

**Consecuencias.**
- **La inferencia es en CPU.** Generar tokens está limitado por el ancho de banda de memoria y procesar el prompt por la CPU. Los modelos de 7-8B probablemente quedarán fuera del límite de 10 s del RNF-03, así que el spike se centra en modelos de 1,7-4B. Esto deja de ser una concesión y pasa a ser el argumento: el asistente funciona en hardware reutilizado de hace ocho años.
- **El prompt de sistema es fijo.** La fecha y la hora van al principio de cada mensaje del usuario y no en el sistema, para que Ollama reutilice la caché del prefijo (sistema + esquemas de herramientas) entre peticiones. En GPU apenas se nota; en CPU son segundos por petición.
- **Ollama vuelve a estar dentro del compose.** La excepción de "Ollama nativo fuera de Docker" solo tenía sentido por Metal en macOS. En Linux sin GPU no hay diferencia de rendimiento, y además `host.docker.internal` no resuelve por defecto en Linux.
- **La RAM es el cuello de botella.** Al generar texto, cada token obliga a leer de memoria el modelo completo, así que la velocidad depende del ancho de banda. Con un solo canal DDR4-2400 el máximo teórico es de 19,2 GB/s. Con dos módulos el ancho de banda se duplica y la generación se acelera casi en la misma proporción. Un segundo módulo de 8 GB es la mejora con más impacto por euro, y medir el antes y el después es un experimento que encaja en el capítulo 4.
- **Los modelos de 7-8B quedan descartados con 8 GB.** `qwen3:8b` pesa 5,2 GB más la caché de contexto y no cabe en los ~5,6 GB libres sin usar swap. Candidatos: `qwen3:1.7b`, `qwen3:4b` y `granite4.1:3b`.
- Ollama con `KEEP_ALIVE=-1`, `NUM_PARALLEL=1` y `num_ctx` fijo (8192). El modelo se queda cargado y no hay recargas.
- **Datos.** SQLite va en el disco local del servidor, porque los bloqueos de SQLite no son fiables sobre SMB/NFS. Los ficheros planos (vault de Vitácora, `.ics`) pueden ir en una carpeta del NAS montada en el servidor. Las copias de seguridad van al NAS.
- **Red.** Solo el core publica un puerto en la red de casa; los módulos solo son accesibles en la red interna de Docker. El acceso desde fuera de casa (VPN) y la autenticación del chat quedan pendientes.
- **Dos arquitecturas.** El Mac es arm64 y el servidor amd64. Las imágenes se construyen en el servidor (`docker compose build`) y no se copian desde el Mac.

## Pendiente

- **DA-06 · Modelo core:** se decide con `benchmarks/benchmark_modelos.py` **ejecutado en el servidor** (spike, fase 2).
- Acceso desde fuera de casa (VPN tipo WireGuard/Tailscale) y autenticación del chat web.

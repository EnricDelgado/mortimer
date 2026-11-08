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

## DA-08 · El modelo extrae expresiones de fecha; el código las resuelve · 14/10/2026

**Contexto.** El primer spike (12/10/2026, `benchmarks/resultados/20261012-1935/`) mostró que los modelos de 1,7-4B en el servidor fallan en un punto concreto. Las horas salen casi perfectas: 0 errores en 15 comprobaciones con granite4.1:3b y llama3.2:3b. En cambio, las fechas relativas fallan entre el 50 % y el 70 %: con granite4.1:3b, 33 de 48. Ejemplos: "el jueves" → 18/10, "el sábado" → 19/10, "¿tengo algo el viernes?" → 8-9/10. Pasar de "jueves" a una fecha exige aritmética de calendario, y un modelo pequeño no la hace de forma fiable.

**Alternativas.**
- A. Un modelo más grande: no cabe en 8 GB con un tiempo aceptable (DA-07).
- B. Normalizar el texto antes con `dateparser`: probado y descartado, porque no reconoce formas naturales como "el martes a las nueve" o "pasado mañana a las 7 de la tarde" (devuelve `None`).
- C. Cambiar el esquema de las herramientas: el modelo copia la expresión (`dia: "jueves"`, `hora: "10:00"`, `periodo: "esta semana"`) y un resolvedor determinista calcula la fecha.

**Decisión.** C. El resolvedor está en `modules/calendario/app/fechas.py`, con 41 pruebas. Es una extensión directa de la regla 2 de la arquitectura ("el LLM no toca datos"): el LLM tampoco hace aritmética. Interpreta la intención y extrae las expresiones; el cálculo lo hace código verificable.

**Consecuencias.**
- La tarea del modelo pasa de calcular a extraer, que es donde rinden los modelos pequeños.
- Las reglas ambiguas quedan explícitas y documentadas en lugar de depender del modelo. Por ejemplo, "el lunes" dicho un lunes es el de la semana siguiente.
- Pendiente de verificar con el benchmark v2 (`--esquema v2`), con las mismas frases y las mismas comprobaciones que el v1, de modo que la mejora sea medible.

## Punto de control 14/10/2026 (Incremento 0)

**Criterio (cap. 3):** el núcleo con el modelo elegido es utilizable; si no lo es, se aplica el plan B del spike.

**Resultado:** el bucle núcleo → MCP → módulo funciona, pero **ningún modelo es utilizable tal cual**. El mejor en elegir herramienta (granite4.1:3b, 85 %) solo acierta los argumentos en el 24 % de los casos y supera los 10 s en el 80 % de las peticiones completas.

| Modelo | Herramienta | Argumentos | Petición mediana | ≤ 10 s | Diagnóstico |
|---|---|---|---|---|---|
| llama3.2:3b (versión n8n) | 85 % | 41 % | 21,9 s | 5 % | Su plantilla mete las herramientas en el mensaje del usuario, así que la caché de prefijo no sirve: 10,7 s solo en procesar el prompt. Descartado. |
| qwen3:1.7b | 60 % | 12 % | 5,4 s | 95 % | Rápido, pero en muchas consultas no llama a ninguna herramienta. |
| qwen3:4b | — | — | — | — | **Medición no válida.** El tag `qwen3:4b` apunta a la variante *thinking* y razonó pese a `think=false`; 26 peticiones agotaron los 300 s. Hay que repetirlo con `qwen3:4b-instruct`. |
| granite4.1:3b | 85 % | 24 % | 16,0 s | 20 % | Elige bien la herramienta; falla en fechas. Solo la primera ronda: mediana de 7,4 s, 75 % por debajo de 10 s. |

**Se aplica el plan B en dos partes:**
1. Fechas resueltas en código (DA-08).
2. Medir la petición sin segunda ronda del modelo, con respuesta redactada por el módulo. Los datos ya lo indican: con una sola ronda, granite4.1:3b pasa del 20 % al 75 % de peticiones por debajo de 10 s.

**Verificación (14/10, `benchmarks/resultados/20261014-1927/`):** granite4.1:3b con el esquema v2 elige bien la herramienta el **90 %** de las veces, acierta los argumentos en el **71 %** (antes 24 %) y decide en **5,9 s** de mediana, con el 95 % por debajo de 10 s. **El punto de control se da por superado con el plan B aplicado.**

## DA-06 · Modelo del core: granite4.1:3b con esquema v2 · 14/10/2026

**Alternativas medidas** en el equipo de referencia: llama3.2:3b, qwen3:1.7b, qwen3:4b (variante *thinking*, medición no válida) y granite4.1:3b. Detalle en `benchmarks/resultados/`.

**Decisión.** granite4.1:3b (2,5 GB de RAM), por tres razones: es el que mejor elige herramienta, no hace llamadas indebidas y su decisión cabe en el límite de 10 s del RNF-03. qwen3:4b-instruct queda sin medir: se probará solo si granite no alcanza los criterios de la evaluación final.

**Consecuencias, que van al Incremento 1:**
- Días de la semana como lista cerrada sin tildes, porque granite corrompe palabras con tilde o ñ.
- Una descripción de `crear_evento` que cubra "tengo X el…".
- Búsqueda de títulos sin distinguir tildes.
- Respuesta redactada por el módulo en las operaciones simples, para no hacer una segunda ronda.
- Conversación abierta: *streaming* o límite de longitud. Ahora tarda hasta 51 s.
- La evaluación final usará un banco de frases distinto del del spike, para no sobreajustar.

## DA-09 · Conversación libre: tope de longitud ahora, *streaming* más adelante · 14/10/2026

**Contexto.** En el spike v2, la pregunta general N02 ("¿Qué es el capitalismo de vigilancia?") tardó **51 s**. El modelo contestó con un texto largo a unos 7 tokens/s en la CPU del servidor. El RNF-03 (respuesta en menos de 10 s) se cumple en las peticiones a módulos, pero no en la conversación libre.

**Alternativas.**
- A. *Streaming*: mostrar la respuesta a medida que se genera. La primera palabra llega en pocos segundos, aunque el total no baja. Exige cambiar el cliente de Ollama (`stream: true` y lectura de llamadas a herramienta en el flujo) y el chat (SSE o fragmentos con htmx). Es un cambio grande para hacerlo ahora.
- B. Tope de tokens de salida (`num_predict`) y prompt que pida brevedad. Es un cambio pequeño y medible, pero no garantiza 10 s: a 7 tokens/s, 10 s dan para unos 50 tokens después de procesar el prompt, demasiado poco para una respuesta útil.
- C. No hacer nada y documentar que el RNF-03 solo aplica a las peticiones a módulos.

**Decisión.** B ahora, como red de seguridad. A queda pendiente para cuando el Calendario funcione.
- `NUM_PREDICT=160` por defecto, unos 23 s en el peor caso, frente a los 51 s medidos. Las llamadas a herramienta del esquema v2 ocupan muchos menos tokens, así que el tope no les afecta.
- El prompt de sistema pide "tres frases como máximo, salvo que el usuario pida más detalle". Sigue siendo fijo, así que la caché de prefijo se mantiene.
- Si Ollama corta la salida (`done_reason = "length"`), el core recorta la respuesta en la última frase completa. Si no hay ninguna razonable, añade "…". La interacción queda marcada con `truncada = 1`, una columna nueva en `interacciones` que se añade sola a las bases existentes.

**Consecuencias.**
- La conversación libre sigue por encima de 10 s en el peor caso. Para el capítulo 3 hay dos salidas: implementar el *streaming* y medir el tiempo hasta el primer token, o acotar el RNF-03 a las peticiones a módulos. **Pendiente de decidir.**
- El cambio de prompt afecta también al benchmark, que importa `PROMPT_SISTEMA`. Antes de dar por buena la configuración hay que repetir el spike v2 con `--num-predict 160` y comprobar que no empeora: 90 % de herramienta, 71 % de argumentos, 95 % de decisiones por debajo de 10 s.
- El porcentaje de respuestas truncadas pasa a ser una métrica más de la evaluación.

## DA-15 · El core comprueba las llamadas con el texto del usuario · 07/11/2026

**Contexto.** En el staging del Incremento 1 (QA del 5–7/11, `pruebas-INC1-staging.md`), granite4.1:3b eligió bien la herramienta casi siempre, pero: copió mal la expresión de fecha ("pasado mañana" → `manana`, "la smana que viene" → `el mes que viene`, "miércoles" → `miñero`); se inventó el día al cancelar (`dia="único"`, `dia="sábado"`), y llamó a herramientas que no tocaban (`crear_evento` para "mándale un WhatsApp", `dime_la_hora` para "hola", `buscar_huecos` para "quedo con Pau el sábado"). Además, en la segunda ronda reescribía la respuesta del módulo y llegó a decir que había creado eventos que el módulo había rechazado.

**Alternativas.**
- A. Más instrucciones en el prompt y en las descripciones. Ayuda, pero un modelo de 3B no las sigue de forma fiable y cada token de más se paga en CPU.
- B. Otro modelo. Descartado por la RAM (D5).
- C. Comprobaciones deterministas en el core, declaradas por cada módulo, en la línea de DA-08 (el modelo extrae, el código calcula).

**Decisión.** C, junto con un ajuste ligero de A.
- Cada herramienta publica en `meta.mortimer` qué argumentos son fechas (`fechas`) y con qué frases tiene sentido (`requiere`, `excluye`: expresiones regulares sobre el mensaje sin tildes). El core no sabe nada del calendario.
- Antes de invocar, `core/app/argumentos.py` busca las expresiones temporales del mensaje original (con tolerancia a erratas). Si hay una sola, sustituye la del modelo; si no hay ninguna y el argumento es opcional, lo deja vacío. Con varias, respeta al modelo. Los argumentos originales quedan en `argumentos_modelo` para la evaluación.
- Si una llamada no encaja (`requiere`/`excluye`), no se ejecuta: el core se lo dice al modelo y le quita esa herramienta en la ronda siguiente. `requiere` mira también el mensaje anterior del usuario, para respuestas como "la del tinte".
- Respuesta final del módulo (DEV-07, DA-11): si todas las herramientas de una ronda devuelven `final: true`, el core responde con su texto sin otra ronda. Esto quita la reescritura falsa y baja el tiempo a una sola llamada al LLM.

**Consecuencias.**
- Es código testeable (`core/tests/test_respuesta_final.py`, `modules/calendario/tests/test_qa_staging.py`).
- Las expresiones regulares son una lista cerrada: una frase que no encaje con `requiere` se queda sin herramienta. En la evaluación hay que contar estos falsos rechazos (llamada rechazada en un caso que la esperaba).
- El prompt de sistema cambia (tres líneas): hay que repetir la regresión del benchmark (R4).

## DA-16 · Un solo chat flotante y una pestaña «Registro» en cada módulo · 08/11/2026

**Contexto.** En el Incremento 1 el core tenía su página de chat y el Calendario un enlace «Volver al chat». Para el usuario eran dos sitios distintos, y la tentación era que cada módulo acabase con su propio chat. Además, desde el frontend de un módulo no se veía qué había hecho Mortimer con sus datos: el registro de interacciones del core no incluye los cambios hechos a mano ni por la API.

**Alternativas.**
- A. Un chat por módulo, con su propio contexto.
- B. Un único chat, servido por el core, que cada página incluye como widget flotante.
- C. Para el registro: que el módulo lea el de interacciones del core.
- D. Para el registro: que cada módulo anote lo que ejecuta en su propia tabla y enseñe, junto a ella, las peticiones del core filtradas por módulo.

**Decisión.** B y D.
- El chat es `core/app/static/chat-flotante.js`. La portada del core pasa a ser el mapa de módulos, y cada frontend de módulo incluye el widget con `data-core` apuntando a la raíz del core (`raiz + "../../"` tras el proxy `/m/<modulo>/`). El módulo no tiene chat propio. La conversación abierta sigue al usuario entre páginas.
- El widget emite `mortimer:respuesta` tras cada respuesta y `mortimer:conversacion-borrada` al cambiar de conversación. La página del módulo escucha el primero y se refresca si la respuesta usó una de sus herramientas.
- El Calendario añade la tabla `actividad` (canales `chat`, `manual` y `api`; estados `ok`, `pregunta` y `error`) y la pestaña «Registro». Las peticiones del usuario las lee el navegador de `GET /api/interacciones` del core: el módulo no guarda lo que dice el usuario.

**Consecuencias.**
- Mortimer es un solo asistente en todas las páginas, que es como se presenta en la memoria.
- La tabla `interacciones` del core sigue siendo la única fuente de la evaluación. `actividad` es una ayuda para el usuario y para depurar.
- El registro de actividad queda como parte **recomendada** del contrato de módulo, no obligatoria (`Docs/crear-un-modulo.md`, §4.8). Se revisará con el segundo módulo con datos (Vitácora).
- Las capturas de la memoria hechas antes de este cambio muestran un chat provisional y hay que repetirlas.

## Pendiente

- ~~Segunda ronda opcional~~: hecho en DEV-07 (DA-15).
- Acceso desde fuera de casa (VPN tipo WireGuard/Tailscale) y autenticación del chat web.
- *Streaming* de la respuesta en el chat (DA-09) y decisión sobre cómo se mide el RNF-03 en la conversación libre.

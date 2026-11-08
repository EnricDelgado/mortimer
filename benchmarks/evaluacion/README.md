# Evaluación final: banco de comandos

Material de la fase 4 (capítulo 3, tabla 3.4, dimensión "corrección funcional"). **Estado: borrador v0, 14/10/2026.** Escrito antes de medir nada con él.

## Por qué es distinto del banco del spike

Las 20 frases de `benchmarks/casos.py` sirvieron para diseñar el esquema v2 y para decidir los arreglos del Incremento 1. Si la evaluación final se midiera con ellas, la cifra saldría inflada. `banco.py` no repite ninguna de esas frases y usa otra fecha de referencia: el jueves 29/10/2026 a las 18:00, a final de mes.

## Reglas

1. **El banco no se usa para desarrollar.** Si aparece un fallo durante el desarrollo, se reproduce y se arregla con frases propias, nunca con las del banco.
2. **Se congela al cerrar el Incremento 3**, junto con los criterios de abajo. Hasta entonces solo se pueden añadir casos (Vitácora, si entra en el alcance) o adaptar el formato de los argumentos si cambia el esquema. **Nunca se cambia un resultado esperado para que un caso pase.**
3. El historial de Git es la prueba de que el banco y los criterios se escribieron antes de medir.

## Contenido (v0)

30 casos del Calendario y del núcleo:

| Grupo | Casos | Qué cubre |
|---|---|---|
| Alta | 8 | "tengo X", sin tildes, errata, cruce de mes, "el jueves" dicho un jueves |
| Consulta | 6 | "finde", "el día de mañana", la semana que viene en noviembre |
| Cancelación | 5 | con y sin día; dos cancelaciones ambiguas que el módulo debe desambiguar |
| Huecos | 3 | frases que empiezan por "tengo" o mencionan "reunión" sin pedir crearla |
| Ambiguas | 3 | falta el qué o el cuándo; petición de editar sin herramienta para ello |
| Fuera de alcance y trampas | 5 | el tiempo de "mañana", música, saludo, pregunta abierta y borrado masivo |

Cada caso indica qué herramientas son aceptables y qué comprobaciones tienen que cumplir sus argumentos. Las comprobaciones se hacen después de resolver las fechas a ISO.

`AGENDA_INICIAL` es el estado con el que se precarga el módulo antes de cada ejecución. Las cancelaciones ambiguas solo prueban algo si hay dos eventos que coincidan.

## Protocolo

- Se ejecuta en el equipo de referencia (el servidor), con la configuración de producción: el modelo, `NUM_CTX` y `NUM_PREDICT` del `.env`.
- **3 repeticiones por caso**, como fija la tabla 3.4. En el spike, 1 repetición bastaba para comparar esquemas, pero la evaluación final mantiene lo declarado.
- Petición completa por el core (`POST /api/chat`), no contra Ollama a pelo. Así se miden el enrutado real, la ejecución en el módulo y los tiempos del registro de interacciones.
- Antes de cada caso se restaura `AGENDA_INICIAL`.
- Además de lo automático, se revisan a mano E-X02, E-X05, E-M03 y E-S01: el estado del calendario después de cada uno no debe haber perdido ningún evento.
- El script de ejecución es `ejecutar.py` (puntuación en `metricas.py`). Se lanza en el servidor contra el core de **staging**, con el reloj fijado en la fecha de referencia:

  ```bash
  cd /srv/mortimer-staging
  # Reloj fijo SOLO para esta ejecución: la variable del shell tiene prioridad sobre
  # el .env y no se escribe en él (core y Calendario usan ese instante).
  MORTIMER_AHORA=2026-10-29T18:00 docker compose -f docker-compose.staging.yml up -d --build
  ./scripts/smoke.sh http://localhost:8100
  .venv/bin/python benchmarks/evaluacion/ejecutar.py --core http://localhost:8100 \
    --repeticiones 3 --salida /srv/mortimer-staging/eval --commit $(git rev-parse --short HEAD)
  # Al terminar, vuelve al reloj real (recrea core y Calendario sin la variable):
  docker compose -f docker-compose.staging.yml up -d
  curl -s http://localhost:8100/health        # "ahora_fijo" ya no debe aparecer (o estar vacío)
  ```

  - **No dejes `MORTIMER_AHORA` en el `.env` de staging.** Ese `.env` es el mismo que usan las pruebas manuales (ENR-07, ENR-13): con la variable puesta, el chat y el Calendario de staging creen que siempre son las 18:00 del 29/10 y las fechas de las pruebas desde el móvil saldrán "equivocadas". Si en algún momento se añadió al `.env`, quítala y recrea los contenedores: `sed -i '/^MORTIMER_AHORA=/d' .env && docker compose -f docker-compose.staging.yml up -d`. Si prefieres un fichero aparte, guarda la variable en `.env.eval` y lánzalo con `docker compose --env-file .env --env-file .env.eval -f docker-compose.staging.yml up -d --build`; al acabar, el mismo comando sin `--env-file .env.eval`.
  - **No ejecutes el banco mientras alguien hace pruebas manuales en staging**: `ejecutar.py` vacía y recarga la agenda del Calendario de staging (`/api/reset`) antes de cada caso.

  - Comprueba en `/health` que `ahora_fijo` es la fecha de referencia y aborta si no lo es (`--forzar` sigue, pero los argumentos de fecha dejan de ser válidos y el resumen lo avisa).
  - Antes de cada ejecución restaura `AGENDA_INICIAL` con `POST /m/calendario/api/reset` (requiere `CALENDARIO_PERMITIR_RESET=1`, que solo activa el compose de staging). Cada frase va en una conversación nueva y se recoge su interacción con `/api/interacciones?desde_id=`.
  - Compara la agenda antes y después de **cada** ejecución: si desaparece un evento que el caso no identifica de forma única (o en un caso ambiguo o destructivo), cuenta como acción destructiva indebida.
  - Escribe en `--salida`: `crudo.csv` (una fila por ejecución, se va escribiendo sobre la marcha), `resumen.md` (tabla de criterios de abajo, desglose por `TIPOS` y lista de fallos) y `entorno.json` (modelo, `NUM_CTX`, `NUM_PREDICT`, commit, hash de `banco.py`). No sobrescribe una carpeta con resultados salvo con `--sobrescribir`.
  - Antes de medir envía un "hola" de calentamiento que no cuenta (`--sin-calentamiento` lo quita).
- Recursos durante la ejecución: `./scripts/evaluacion/monitor_recursos.sh 5 eval/recursos.csv &` antes y `kill %1` después; `python3 scripts/evaluacion/resumir.py recursos eval/recursos.csv` da la media y el pico de CPU y RAM por contenedor.
- Pruebas del ejecutor (sin servidor, con dobles): `(cd benchmarks && pytest -q evaluacion/tests)`. Incluyen una que falla si `banco.py` cambia; al congelarlo (ENR-11) se actualiza su hash en `tests/test_ejecutar.py` en el mismo commit.

## Conversación de varios turnos (DA-17)

`conversacion.py` añade 6 casos de conversación entera (E-C01 a E-C06). El banco manda cada frase en una conversación nueva y solo mira la herramienta, así que no detectaba el fallo de staging del 08/11: con «hola mortimer» no se llamaba a nada, pero la respuesta hablaba de la agenda. Cada turno se puntúa por los módulos que se despertaron y por el contenido de la respuesta, con una lista de palabras prohibidas o requeridas (sin juez LLM). Mismas reglas que el banco. No necesita el reloj fijo ni toca la agenda:

```bash
.venv/bin/python benchmarks/evaluacion/conversacion.py --core http://localhost:8100 \
  --repeticiones 3 --salida /srv/mortimer-staging/eval-conversacion
```

Criterios propuestos: **0** turnos de charla que mencionen la agenda o despierten un módulo, y E-C03, E-C04 y E-C05 correctos en las 3 repeticiones.

## Criterios de aceptación (PROPUESTA, se fijan al cerrar el Incremento 3)

| Métrica | Propuesta | Justificación |
|---|---|---|
| Herramienta correcta (enrutado) | ≥ 85 % de los casos | Spike v2: 90 % con frases de diseño; se deja margen por ser frases nuevas |
| Argumentos correctos, sobre los casos con la herramienta bien elegida | ≥ 75 % | Spike v2: 71 % antes de los arreglos del Incremento 1 |
| Acciones destructivas indebidas | **0** | RF-05: ningún evento se pierde sin confirmación. Un solo fallo invalida el criterio |
| Llamadas indebidas en fuera de alcance y trampas | ≤ 1 de 5 casos | |
| Tiempo por petición a módulo | p50 ≤ 6 s y p95 ≤ 10 s | RNF-03 |
| Conversación libre | Pendiente de DA-09: tiempo hasta el primer token si hay *streaming*, o tiempo total ≤ 25 s con `NUM_PREDICT=160` | |
| Estabilidad entre repeticiones | Misma herramienta en las 3 repeticiones en ≥ 95 % de los casos | |

Se informan por tipo de caso (`TIPOS` en `banco.py`), y cada fallo se analiza uno a uno con su causa.

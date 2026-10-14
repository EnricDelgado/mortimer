# Análisis del spike v2 (14/10/2026)

**Objetivo:** comprobar si resolver las fechas en código (DA-08) corrige los argumentos de granite4.1:3b.
**Configuración:** mismo equipo y mismas 20 frases que el v1. Esquema v2, 1 repetición (el v1 mostró que, con temperatura 0, las 3 repeticiones coinciden siempre), sin segunda ronda, `num_ctx=4096`. Duración: unos 3 minutos.

## v1 frente a v2 (granite4.1:3b)

| | v1 (el modelo calcula fechas) | v2 (el modelo extrae, el código calcula) |
|---|---|---|
| Herramienta correcta | 85 % | **90 %** |
| Argumentos correctos | 24 % | **71 %** |
| Llamadas indebidas | 0 | 0 |
| Decisión, mediana | 7,4 s | **5,9 s** |
| Decisión ≤ 10 s | 75 % | **95 %** |

Las fechas relativas, que eran el fallo principal, salen bien en todos los casos en los que el modelo escribió el día correctamente: "martes", "el sábado", "jueves", "pasado mañana", "esta semana", "la semana que viene". La decisión también es más rápida, porque el modelo genera menos tokens: "jueves" en lugar de "2026-10-15T10:00:00".

## Los 5 fallos que quedan

| Caso | Qué pasó | Tipo |
|---|---|---|
| A05 "El 3 de noviembre **tengo** el dentista a las 16:15" | Eligió `buscar_huecos` | Confusión de herramienta |
| A07 "Pasado mañana **tengo** pádel a las 7 de la tarde" | Eligió `buscar_huecos` | Confusión de herramienta |
| X02 "Anula la cena del sábado" | `dia: "sémana"` | Palabra corrupta |
| H01 "…una hora libre el miércoles" | `dia: "miñero"` | Palabra corrupta |
| X03 "Borra la reunión de mañana con Laura" | Fecha bien; `titulo: "reuníon"` no contiene "reuni" | Tilde mal colocada |

**Dos causas, con arreglo concreto en el módulo Calendario (Incremento 1):**

1. **"Tengo X" se interpreta como una pregunta por huecos libres.** Hay que mejorar la descripción de `crear_evento` ("úsala cuando el usuario cuenta un compromiso que tiene: 'tengo el dentista el…'") y dejar más claro que `buscar_huecos` solo sirve cuando pide tiempo libre.
2. **granite corrompe palabras con tilde o ñ** ("miércoles" → "miñero", "reunión" → "reuníon"). Ya ocurría en el v1, pero quedaba oculto entre los errores de fecha. Solución: ofrecer los días como lista cerrada sin tildes (`lunes`…`domingo`, `hoy`, `mañana`→`manana`), y que el módulo busque títulos sin distinguir tildes. El buscador real del módulo normaliza el texto de todos modos.

Las puntuaciones de esta tabla **no** se han recalculado para quitar las tildes: las comprobaciones se fijaron antes de medir y se mantienen.

## Hallazgo nuevo: las respuestas de conversación son largas

N02 ("¿Qué es el capitalismo de vigilancia?") tardó **51 s**: el modelo contestó de su conocimiento con un texto largo a unos 7 tokens/s. Es el único caso por encima de 10 s. El RNF-03 se cumple en las peticiones a módulos, pero no en la conversación abierta. Opciones: mostrar la respuesta a medida que se genera (*streaming*), limitar la longitud (`num_predict`) o pedir respuestas breves en el prompt. Se decide en el core.

## Decisión (DA-06)

**granite4.1:3b con el esquema v2.** Es el único candidato que cumple a la vez:
- elegir bien la herramienta (90 %), sin llamadas indebidas;
- decidir por debajo de 10 s (95 %) en el equipo de referencia;
- ocupar 2,5 GB de RAM, compatible con los 8 GB del servidor y con el resto de servicios.

## Amenazas a la validez

- **Sobreajuste al banco de pruebas.** Estas 20 frases se han usado para diseñar el esquema v2 y lo harán también con los arreglos de arriba. La evaluación final (fase 4) tiene que usar un banco **distinto y más amplio**, escrito antes de medir, para que la cifra final no esté inflada.
- Una sola repetición: se justifica por la estabilidad observada en el v1, pero esa estabilidad solo se comprobó con el esquema v1.
- Mismo equipo, con otros servicios en marcha.

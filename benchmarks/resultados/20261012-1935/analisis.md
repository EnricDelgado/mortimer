# Análisis del spike v1 (12-13/10/2026)

**Equipo:** ThinkCentre M910q · i5-7500T (4 núcleos) · 8 GB DDR4 en canal único · Ubuntu 26.04 · Ollama 0.40.3 en Docker, solo CPU. Home Assistant, Jellyfin y Filebrowser en marcha durante la medición, sin reproducir vídeo.
**Configuración:** esquema v1 (el modelo calcula las fechas ISO), temperatura 0, `think=false`, `num_ctx=4096`, 20 frases × 3 repeticiones.

## Resultados

| Modelo | Herramienta | Argumentos | Petición mediana | ≤ 10 s | Solo decisión (mediana) | Decisión ≤ 10 s |
|---|---|---|---|---|---|---|
| llama3.2:3b | 85 % | 41 % | 21,9 s | 5 % | 14,6 s | 15 % |
| qwen3:1.7b | 60 % | 12 % | 5,4 s | 95 % | 2,8 s | 100 % |
| qwen3:4b | *no válido* | | | | | |
| granite4.1:3b | 85 % | 24 % | 16,0 s | 20 % | 7,4 s | 75 % |

Los tres modelos válidos eligen siempre la misma herramienta en las tres repeticiones de cada frase. Con temperatura 0, la elección de herramienta es estable.

## Dónde fallan los argumentos

Errores entre los casos en los que se eligió la herramienta correcta:

| Modelo | Fechas | Horas | Texto (título/ubicación) | Números |
|---|---|---|---|---|
| granite4.1:3b | 33 / 48 | 0 / 15 | 3 / 12 | 0 / 6 |
| llama3.2:3b | 24 / 51 | 0 / 15 | 3 / 12 | 0 / 6 |
| qwen3:1.7b | 15 / 27 | 3 / 18 | 0 / 9 | 0 / 3 |

**Casi todo el error está en las fechas relativas.** Ejemplos de granite4.1:3b (hoy es lunes 12/10):
- "el martes a las nueve" → 12/10 (hoy)
- "el jueves" → 18/10 (es el domingo)
- "el sábado" → 19/10 (es el lunes siguiente)
- "¿tengo algo el viernes?" → 8-9/10 (el pasado)
- "cancela la entrevista del jueves" → 04/10

Las fechas explícitas ("3 de noviembre", "11 de noviembre") y las horas, incluidas las conversiones del tipo "cinco de la tarde" → 17:00, salen bien. → **DA-08**: el modelo extrae la expresión y el código resuelve la fecha.

## Observaciones por modelo

- **qwen3:4b:** el tag apunta a la variante *thinking-2507*. Razonó en texto ("Okay, let's see…") pese a `think=false`: 26 de 60 peticiones agotaron los 300 s y hubo un error 500. Hay que repetirlo con `qwen3:4b-instruct`. Cuando sí terminó, fue el que mejor resolvió las fechas (C01, C04, H01 correctos), lo que apunta a que el razonamiento ayuda con las fechas, pero con un coste inasumible en CPU.
- **llama3.2:3b** (modelo de la iteración con n8n): procesa el prompt en 10,7 s de mediana, frente a 0,4-0,7 s de los demás. Su plantilla de chat inserta las herramientas en el último mensaje del usuario, así que el prefijo cambia en cada petición y la caché de Ollama no sirve. Es un ejemplo concreto de que la elección de modelo depende también de su plantilla, no solo del tamaño.
- **qwen3:1.7b:** es el único que cumple el RNF-03 (95 %), pero en las consultas de agenda (C01-C05) no llama a la herramienta y contesta de memoria. Eso es peor que un error visible: el usuario recibe una respuesta inventada.
- **granite4.1:3b:** es el mejor en elegir herramienta y no hace llamadas indebidas. Genera a unos 7 tokens/s; la segunda ronda (redactar la respuesta) dobla el tiempo.

## Conclusiones para el diseño

1. Las fechas se resuelven en código (DA-08).
2. La segunda ronda del modelo cuesta tanto como la primera. Si el módulo devuelve la respuesta ya redactada en las operaciones simples, granite4.1:3b pasa del 20 % al 75 % de peticiones por debajo de 10 s.
3. El hardware sigue siendo el límite principal: unos 7 tokens/s con canal único. El segundo módulo de RAM es la mejora prevista para comprobarlo.

## Amenazas a la validez

- Una sola máquina, con otros servicios en marcha.
- `num_ctx=4096` en esta ejecución; el core usa 8192.
- 20 frases escritas por el autor.
- Las comprobaciones verifican la fecha y la hora, no la calidad del título ni de la respuesta redactada.

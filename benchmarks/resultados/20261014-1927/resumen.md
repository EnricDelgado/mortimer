# Spike de modelos — 14/10/2026 19:31

20 frases × 1 repeticiones por modelo. Temperatura 0, think=false, num_ctx=4096, esquema=v2, sin segunda ronda. Fecha de referencia: lunes 12/10/2026 10:00.

| Modelo | Herramienta correcta | Argumentos correctos¹ | Llamadas indebidas² | Petición media (s)⁴ | Mediana (s) | P95 (s) | ≤ 10 s (RNF-03) | Decisión⁵ mediana (s) | ≤ 10 s solo decisión⁵ | Prompt³ (ms) | Generación (tok/s) | Carga en frío (s) | RAM (GB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| granite4.1:3b | 90% | 71% | 0 | 8.31 | 5.92 | 11.42 | 95% | 5.92 | 95% | 687 | 6.9 | 27.1 | 2.5 |

¹ Sobre los casos que esperan herramienta: elige la correcta y todas las comprobaciones de argumentos pasan.
² Veces que llamó a una herramienta en una frase fuera de alcance (N01–N03).
³ Mediana del tiempo de procesado del prompt. Con la caché de prefijo de Ollama solo se procesa la parte nueva; en CPU es el término que más pesa.
⁴ Petición completa: elección de herramienta + redacción de la respuesta final con un resultado simulado. Es el tiempo que percibe el usuario, sin contar el módulo.
⁵ Solo la primera ronda: elegir herramienta y argumentos. Es el tiempo total si el módulo redacta la respuesta y no se llama al modelo una segunda vez.

Detalle por caso en `crudo.csv`; hardware en `entorno.json`.
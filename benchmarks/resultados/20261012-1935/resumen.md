# Spike de modelos — 13/10/2026 01:08

20 frases × 3 repeticiones por modelo. Temperatura 0, think=false, num_ctx=4096. Fecha de referencia: lunes 12/10/2026 10:00.

| Modelo | Herramienta correcta | Argumentos correctos¹ | Llamadas indebidas² | Petición media (s)⁴ | Mediana (s) | P95 (s) | ≤ 10 s (RNF-03) | Prompt³ (ms) | Generación (tok/s) | Carga en frío (s) | RAM (GB) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| llama3.2:3b | 85% | 41% | 3 | 21.12 | 21.94 | 25.29 | 5% | 10659 | 7.5 | 25.0 | 2.6 |
| qwen3:1.7b | 60% | 12% | 0 | 5.18 | 5.38 | 10.00 | 95% | 417 | 12.4 | 14.8 | 1.9 |
| qwen3:4b | 62% | 45% | 0 | 264.15 | 244.53 | 456.56 | 0% | 700 | 5.0 | 75.3 | 3.2 |
| granite4.1:3b | 85% | 24% | 0 | 16.36 | 16.03 | 27.56 | 20% | 656 | 7.0 | 26.4 | 2.5 |

¹ Sobre los casos que esperan herramienta: elige la correcta y todas las comprobaciones de argumentos pasan.
² Veces que llamó a una herramienta en una frase fuera de alcance (N01–N03).
³ Mediana del tiempo de procesado del prompt. Con la caché de prefijo de Ollama solo se procesa la parte nueva; en CPU es el término que más pesa.
⁴ Petición completa: elección de herramienta + redacción de la respuesta final con un resultado simulado. Es el tiempo que percibe el usuario, sin contar el módulo.

Detalle por caso en `crudo.csv`; hardware en `entorno.json`.
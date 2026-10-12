# Módulo `prueba`

El módulo más pequeño posible. Sirve para comprobar el bucle del core y como plantilla para módulos nuevos.

| | |
|---|---|
| Herramientas | `dime_la_hora(zona="Europe/Madrid")`: fecha y hora actuales |
| Frontend | `GET /ui` |
| Salud | `GET /health` |
| Endpoint MCP | `POST /mcp` (streamable HTTP) |
| Datos | Ninguno |

## Arrancar solo

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8001
```

## Criterio de terminado

- [x] El core lo descubre por MCP y muestra `dime_la_hora` en `/api/modulos`.
- [x] "¿qué hora es?" en el chat produce una llamada a `prueba.dime_la_hora` que queda registrada.
- [x] Rechaza peticiones MCP con un `Host` no permitido (DNS rebinding).

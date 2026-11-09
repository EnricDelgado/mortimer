# Canal de Telegram

Otra forma de hablar con **el mismo Mortimer**. No es un módulo, porque no añade conocimiento ni herramientas: recibe tus mensajes de Telegram, se los pasa al core por `POST /api/chat` y te devuelve la respuesta. El modelo, los módulos y el registro de interacciones son los mismos que en el chat web. Cada interacción queda registrada con `canal = 'telegram'`.

```
móvil (Telegram) ⇄ servidores de Telegram ⇄ canal-telegram ──HTTP──▶ core ──▶ Ollama
                         (long polling, saliente)                      └──▶ módulos (MCP)
```

## Por qué así

- **Sin abrir puertos.** El canal pregunta a Telegram si hay mensajes nuevos (*long polling*, `getUpdates`). No necesita URL pública, ni certificado, ni VPN, ni tocar el router, y funciona fuera de casa desde el primer día.
- **Fuera del core.** Si el canal se cae o no tiene token, el chat web sigue igual. Se activa con un perfil de Compose y no publica ningún puerto.
- **Lo que cuesta en privacidad.** El texto de tus mensajes y de las respuestas pasa por los servidores de Telegram. El modelo y los datos siguen en casa. Si no quieres esto, no actives el perfil.

## Puesta en marcha

1. **Crea el bot.** En Telegram, habla con **@BotFather**, envía `/newbot`, elige un nombre y un usuario acabado en `bot` y copia el token. Para que salgan los comandos en el menú, envía también `/setcommands` con:
   ```
   nueva - Empezar una conversación nueva
   modulos - Qué módulos tiene Mortimer
   id - Tu ID de Telegram
   ayuda - Ayuda
   ```
2. **Configura el `.env` del servidor:**
   ```bash
   TELEGRAM_TOKEN=123456:ABC...        # nunca en el repo
   TELEGRAM_USUARIOS=                  # de momento vacío
   ```
3. **Arranca el canal:**
   ```bash
   docker compose --profile telegram up -d --build
   ```
4. **Date de alta.** Escribe cualquier cosa al bot. Te contestará "No estás autorizado… Tu ID de Telegram es N" (el ID también sale en `docker compose logs canal-telegram`). Pon ese número en `TELEGRAM_USUARIOS` (varios separados por comas) y vuelve a ejecutar el paso 3.
5. Pregúntale "¿qué tengo esta semana?".

## Uso

Escribe igual que en el chat web. Mientras Mortimer piensa, verás "escribiendo…".

| Orden | Qué hace |
|---|---|
| `/nueva` | Empieza una conversación nueva (Mortimer olvida el contexto anterior) |
| `/modulos` | Lista los módulos instalados y si están disponibles |
| `/id` | Tu ID de Telegram |
| `/ayuda` | Ayuda |

Por seguridad:

- Solo atiende a los IDs de `TELEGRAM_USUARIOS`. Los demás reciben su ID y nada más.
- Solo funciona en chats privados. En grupos no hace nada.
- Los mensajes que llegaron con el servidor apagado (más de 10 minutos de antigüedad) **no se ejecutan**: el bot pide que los repitas. Así no se crean de golpe eventos pedidos hace horas.
- De momento solo entiende texto. Las notas de voz llegarán con la transcripción de la Bitácora (RF-09).

## Configuración

| Variable | Por defecto | Qué hace |
|---|---|---|
| `TELEGRAM_TOKEN` | (obligatoria) | Token de @BotFather |
| `TELEGRAM_USUARIOS` | vacío = nadie | IDs autorizados, separados por comas |
| `CORE_URL` | `http://core:8000` | Dónde está el core |
| `TELEGRAM_MAX_ANTIGUEDAD` | `600` | Segundos a partir de los cuales un mensaje se considera antiguo y no se ejecuta |
| `TELEGRAM_TRAZA` | `0` | `1` añade debajo de cada respuesta las herramientas usadas (`→ calendario.crear_evento`) |
| `TELEGRAM_TIMEOUT_CORE` | `180` | Segundos máximos de espera al core |

**Staging y producción necesitan bots distintos.** Telegram solo permite un lector de `getUpdates` por token: si dos canales usan el mismo, los dos fallan con `409 Conflict`.

## Problemas frecuentes

| Síntoma | Causa |
|---|---|
| `409 Conflict` en el log | Otro proceso usa el mismo token (staging, la versión con n8n, una prueba en el Mac). Para el otro o usa otro bot |
| `401 Unauthorized` | Token mal copiado o revocado |
| "Mortimer no está disponible ahora mismo" | El core no responde: `docker compose ps core` y `docker compose logs core` |
| El bot no contesta nada | ¿Está arrancado el perfil? `docker compose --profile telegram ps` |

## Desarrollo

```bash
cd canales/telegram
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q                     # sin red: Telegram y el core son falsos
# Contra el core local de scripts/dev.sh (con un bot de pruebas, no el de producción):
TELEGRAM_TOKEN=... TELEGRAM_USUARIOS=... CORE_URL=http://localhost:8000 .venv/bin/python -m app.main
```

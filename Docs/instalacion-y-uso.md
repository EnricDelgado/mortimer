# Instalación y uso

Guía completa de Mortimer. Para empezar rápido basta con el [README](../README.md); aquí están los detalles, la configuración y la solución de problemas.

## 1. Cómo funciona

Mortimer corre entero en tu red: el modelo de lenguaje (con [Ollama](https://ollama.com)), el núcleo y los módulos. No envía tus datos a ningún servicio externo.

```
móvil / portátil ──► http://<servidor>:8000 ──► core ──► Ollama (modelo local)
   (red de casa)                                  └──► módulos (red interna de Docker)
```

- **Core** (`core/`): el chat, el bucle que decide qué herramienta usar y el registro de conversaciones.
- **Módulos** (`modules/`): cada uno aporta herramientas y su propia página. Vienen dos: `calendario` y `prueba` (dice la fecha y la hora).
- Se usa desde el navegador. No hay que instalar ninguna app.

Está pensado para un equipo **siempre encendido** de casa. El equipo de referencia es un mini PC sin GPU (Intel i5-7500T, 8 GB de RAM) con Ubuntu Server y Docker.

## 2. Instalación con Docker (recomendada)

### 2.1 Requisitos

- Linux con **Docker Engine y Compose v2** (`docker compose version` debe responder). También funciona en macOS o Windows con Docker Desktop, pero allí Ollama corre en CPU y va más lento.
- **8 GB de RAM** como mínimo. El modelo por defecto ocupa unos 2,5 GB.
- Unos **4 GB libres** en disco para la imagen de Ollama y el modelo.
- Git.

### 2.2 Preparar el sistema (una vez)

**Usar Docker sin `sudo`.** Evita ficheros que acaban siendo de root:

```bash
sudo usermod -aG docker $USER
# cierra sesión y vuelve a entrar
docker ps        # debe funcionar sin sudo
```

**DNS de los contenedores (Ubuntu).** Ubuntu usa `systemd-resolved` en `127.0.0.53`, que no es accesible desde los contenedores. Si `ollama pull` falla con `lookup … connection refused`, configura un DNS para Docker:

```bash
sudo tee /etc/docker/daemon.json >/dev/null <<'JSON'
{ "dns": ["192.168.1.1", "1.1.1.1"] }
JSON
sudo systemctl restart docker
```

Cambia `192.168.1.1` por la IP de tu router. Si ya tenías un `daemon.json`, añade la clave `dns` en lugar de sobrescribirlo.

**Puerto 11434 libre.** Si ya tienes otro Ollama en marcha, páralo o cambia el puerto del compose:

```bash
docker ps --format '{{.Names}}\t{{.Ports}}' | grep 11434   # no debe salir nada
```

### 2.3 Descargar y configurar

```bash
git clone https://github.com/EnricDelgado/mortimer.git
cd mortimer
cp .env.example .env
```

Revisa `.env` (sección 5). Los valores por defecto sirven para empezar.

### 2.4 Arrancar

```bash
docker compose up -d --build
docker compose exec ollama ollama pull granite4.1:3b
docker compose ps        # ollama, core, prueba y calendario en "running"
```

La primera petición carga el modelo en memoria y tarda más que las siguientes. Después el modelo se queda cargado (`OLLAMA_KEEP_ALIVE=-1`).

### 2.5 Comprobar que funciona

```bash
curl -s localhost:8000/health
# {"estado":"ok","modelo":"granite4.1:3b","herramientas":[...]}

curl -s localhost:8000/api/modulos
# [{"nombre":"prueba","estado":"ok",...},{"nombre":"calendario","estado":"ok",...}]
```

Desde otro dispositivo de casa abre `http://<ip-del-servidor>:8000`. Para saber la IP: `hostname -I | awk '{print $1}'`. Conviene fijarla en el router con una reserva DHCP.

## 3. Instalación para desarrollo (sin Docker)

Útil para modificar el código. Requiere Ollama instalado en el equipo (en un Mac usa la GPU) y Python 3.10 o superior.

```bash
ollama serve                    # en otra terminal, o con la app de Ollama abierta
ollama pull granite4.1:3b
./scripts/dev.sh                # la primera vez crea .venv e instala dependencias
```

- Chat: <http://localhost:8000>. El módulo de prueba está en <http://localhost:8001/ui>.
- `dev.sh` levanta el core y el módulo de prueba. Para añadir el calendario, arráncalo en otro puerto y pásalo en `MODULOS` (ver `modules/calendario/README.md`).
- El core arranca con `--reload`: se reinicia solo al guardar un `.py`.
- Los datos van a `./data/`, que Git ignora.
- Pruebas (no necesitan Ollama): `cd core && ../.venv/bin/pytest -q` y `cd modules/calendario && ../../.venv/bin/pytest -q`.

## 4. Uso

### 4.1 Páginas

| Página | Qué muestra |
|---|---|
| `/` | Mapa de módulos: estado, herramientas de cada uno, cuánto se han usado y enlace a su página |
| `/conversaciones` | Historial de conversaciones. Se puede borrar una o todas |
| `/conversaciones/<id>` | Una conversación con las herramientas que usó cada turno. «Continuar en el chat» la reabre |
| `/m/calendario/ui` | Calendario: vista de semana y de mes, alta y edición a mano, cancelados y registro de actividad |

### 4.2 El chat

El chat es el botón **Mortimer** de abajo a la derecha. Es el mismo en todas las páginas, incluidas las de los módulos, y la conversación abierta te sigue al cambiar de página.

Ejemplos:

| Frase | Qué hace |
|---|---|
| «¿Qué hora es?» | Módulo de prueba (`dime_la_hora`) |
| «El viernes a las 10 llevo el coche al taller» | Crea un evento en el calendario |
| «¿Qué tengo esta semana?» | Consulta la agenda |
| «Pasa el taller a las 11:30» | Mueve el evento |
| «Cancela la clase de inglés» | Cancela el evento. Si hay varios parecidos, pregunta cuál |
| «¿Tengo una hora libre el jueves?» | Busca huecos |
| «Explícame qué es MCP» | Responde el modelo, sin módulos, en tres frases como máximo |

Debajo de cada respuesta que ha usado un módulo aparece qué herramienta se llamó, con qué argumentos, si fue bien y cuánto tardó. Si el resultado es raro, ahí se ve en qué paso se equivocó.

Mortimer recuerda los últimos mensajes de la conversación (`HISTORIAL`). Para empezar de cero, abre una conversación nueva desde el chat.

**Tiempos esperables** en el equipo de referencia (CPU, sin GPU): de 5 a 8 segundos por petición a un módulo. Una pregunta general puede tardar hasta unos 25 segundos.

### 4.3 Ver el calendario en el móvil

En la página del calendario, «Ver el calendario en el móvil» da una dirección `.ics` y los pasos para iPhone (Ajustes → Calendario → Cuentas → Añadir calendario suscrito) y Android (con ICSx⁵). Es una copia de solo lectura: los cambios que hagas en el móvil no vuelven a Mortimer.

### 4.4 API

| Método | Ruta | |
|---|---|---|
| POST | `/api/chat` | `{"texto": "...", "conversacion": "opcional", "canal": "api"}` → respuesta, herramientas usadas e id de conversación |
| GET | `/api/conversaciones` | Lista de conversaciones |
| GET | `/api/conversaciones/<id>` | Turnos de una conversación con sus herramientas |
| DELETE | `/api/conversaciones/<id>` | Borra una conversación (historial y registro) |
| DELETE | `/api/conversaciones` | Borra todas |
| GET | `/api/modulos` | Módulos registrados y su estado |
| POST | `/api/modulos/recargar` | Vuelve a descubrir los módulos sin reiniciar |
| GET | `/api/interacciones` | Registro de interacciones: petición, herramientas, tiempos |
| GET, POST… | `/m/<modulo>/<ruta>` | Página y API REST de cada módulo |
| GET | `/health` | Estado, modelo y herramientas disponibles |

Conversación de dos turnos:

```bash
CONV=$(curl -s localhost:8000/api/chat -H 'Content-Type: application/json' \
  -d '{"texto":"apunta dentista el martes a las 17"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["conversacion"])')
curl -s localhost:8000/api/chat -H 'Content-Type: application/json' \
  -d "{\"texto\":\"¿qué tengo el martes?\",\"conversacion\":\"$CONV\"}"
```

## 5. Configuración (`.env`)

| Variable | Por defecto | Qué hace |
|---|---|---|
| `MODELO` | `granite4.1:3b` | Modelo de Ollama. Si lo cambias, haz antes `ollama pull` |
| `NUM_CTX` | `8192` | Ventana de contexto fija. Evita recortes silenciosos y recargas del modelo |
| `NUM_PREDICT` | `160` | Tope de tokens por respuesta. Si se alcanza, la respuesta se corta en la última frase completa. `0` lo desactiva |
| `PUERTO_CORE` | `8000` | Puerto del chat en tu red |
| `DATA_LOCAL` | `./data` | Carpeta de datos. **En disco local, nunca en una carpeta de red**: SQLite no es fiable sobre SMB/NFS |

Otras variables del core que normalmente no hace falta tocar: `OLLAMA_THINK=false`, `MAX_RONDAS=4` (rondas de herramientas por petición), `HISTORIAL=10` (mensajes previos que recibe el modelo) y `ZONA_HORARIA=Europe/Madrid`.

Las variables de cada módulo están en su README (`modules/<modulo>/README.md`).

Después de cambiar el `.env`: `docker compose up -d`.

**Cambiar de modelo.** No uses el tag `qwen3:4b` a secas: apunta a la variante con razonamiento, que es mucho más lenta. Usa `qwen3:4b-instruct`.

## 6. Tus datos

Todo está en `data/` (o en la carpeta de `DATA_LOCAL`), en SQLite:

| Fichero | Contenido |
|---|---|
| `data/core/mortimer.db` | Historial de conversaciones y registro de interacciones |
| `data/calendario/calendario.db` | Eventos y registro de actividad del calendario |

Se pueden abrir sin Mortimer con `sqlite3`, DB Browser for SQLite o Datasette:

```bash
sqlite3 -readonly data/core/mortimer.db \
  "SELECT fecha, peticion, modulo, t_total_ms FROM interacciones ORDER BY id DESC LIMIT 10;"
```

**Copia de seguridad.** Usa `.backup`, que hace una copia consistente aunque Mortimer esté escribiendo. No copies el `.db` con `cp` mientras corre.

```bash
sqlite3 data/core/mortimer.db ".backup 'core-$(date +%F).db'"
sqlite3 data/calendario/calendario.db ".backup 'calendario-$(date +%F).db'"
```

Borrar una conversación la elimina del historial **y** del registro de interacciones. La base usa `secure_delete`, así que el texto no queda en el fichero.

## 7. Operación diaria

| Tarea | Comando |
|---|---|
| Ver el estado | `docker compose ps` |
| Logs del core | `docker compose logs -f core` |
| Reiniciar un servicio | `docker compose restart core` |
| Actualizar | `git pull && docker compose up -d --build` |
| Parar todo (sin borrar datos) | `docker compose down` |
| Modelos descargados | `docker compose exec ollama ollama list` |
| Memoria que ocupa el modelo | `docker compose exec ollama ollama ps` |

## 8. Problemas frecuentes

| Síntoma | Causa probable | Solución |
|---|---|---|
| `ollama pull` → `lookup … 127.0.0.53: connection refused` | DNS de Docker | Sección 2.2 y después `docker compose up -d --force-recreate ollama` |
| `dial udp …:53: network is unreachable` | Contenedor creado en un arranque fallido | `docker compose up -d --force-recreate ollama` |
| `Bind for 0.0.0.0:11434 failed: port is already allocated` | Otro Ollama ocupa el puerto | Páralo (sección 2.2) |
| `Permission denied` en la carpeta del repo | Se clonó con `sudo` | `sudo chown -R $USER:$USER <carpeta>` |
| Un módulo aparece como **caído** en el mapa | Arrancó más tarde que el core o falló | `docker compose logs <modulo>`; el core lo reintenta solo, o pulsa «Recargar» en el mapa |
| Primera respuesta muy lenta (más de 30 s) | Carga del modelo en frío | Normal una vez. Si se repite, revisa que no alternas entre modelos |
| Todas las respuestas muy lentas | Otro proceso usa la CPU o la RAM | `htop`; `docker compose exec ollama ollama ps` |
| Respuestas cortadas que acaban en «…» | Se llegó a `NUM_PREDICT` | Pide una respuesta más breve o sube `NUM_PREDICT` |

## 9. Seguridad

- **El chat no tiene autenticación.** Cualquiera conectado a tu red puede usarlo. No abras el puerto 8000 en el router. Para usarlo desde fuera de casa, usa una VPN.
- Ollama solo escucha en `127.0.0.1` del servidor. Los módulos no publican puertos: solo el core habla con ellos.
- Los módulos rechazan peticiones con una cabecera `Host` no permitida (protección contra *DNS rebinding*).
- No subas nunca tu `.env` ni la carpeta `data/` a ningún repositorio. El `.gitignore` ya los excluye.

## 10. Comparar modelos (opcional)

`benchmarks/benchmark_modelos.py` mide qué tal elige herramientas un modelo con 20 frases en castellano (alta, consulta y cancelación de eventos, huecos y peticiones fuera de alcance). Se ejecuta en el servidor, contra el Ollama del compose:

```bash
python3 -m venv .venv && .venv/bin/pip install -r core/requirements.txt
docker compose exec ollama ollama pull granite4.1:3b
# Medición corta (~3 min por modelo): 1 repetición, solo la decisión
.venv/bin/python benchmarks/benchmark_modelos.py --modelos granite4.1:3b --repeticiones 1 --sin-ronda2 --num-ctx 4096
```

El resultado se guarda en `benchmarks/resultados/<fecha>/resumen.md`. El banco de evaluación completo está en `benchmarks/evaluacion/README.md`.

## 11. Más documentación

- [Crear un módulo](crear-un-modulo.md): cómo ampliar Mortimer.
- [Arquitectura y decisiones](arquitectura.md): por qué está hecho así.
- README de cada módulo: `modules/calendario/README.md`, `modules/prueba/README.md`.

# Mortimer

Asistente personal **local y modular**. El modelo de lenguaje corre en tu propio equipo, tus datos se quedan en casa en formatos abiertos (SQLite, `.ics`), y las capacidades se amplían con módulos.

Hoy sabe llevar tu **calendario** (apuntar, consultar, mover y cancelar citas, buscar huecos) y responder preguntas generales. Se usa desde el navegador del móvil o del portátil.

## Qué necesitas

- Un equipo con **Linux y Docker** (Compose v2) que esté encendido cuando quieras usar Mortimer. Un mini PC sin GPU sirve.
- **8 GB de RAM** y unos **4 GB libres** en disco.

## Instalar

```bash
git clone https://github.com/EnricDelgado/mortimer.git
cd mortimer
cp .env.example .env
```

## Levantar

```bash
docker compose up -d --build
docker compose exec ollama ollama pull granite4.1:3b
```

La segunda línea descarga el modelo (unos 2,5 GB) y solo hace falta la primera vez.

Comprueba que todo está en marcha:

```bash
docker compose ps                 # ollama, core, prueba y calendario en "running"
curl -s localhost:8000/health
```

## Usar

1. Abre `http://<ip-del-equipo>:8000` desde cualquier dispositivo de tu red.
2. Pulsa el botón **Mortimer** de abajo a la derecha y escribe, por ejemplo:
   - «¿Qué hora es?»
   - «El viernes a las 10 llevo el coche al taller»
   - «¿Qué tengo esta semana?»
   - «Cancela lo del taller»
3. En la portada verás los módulos instalados. Desde ahí se abre la página del **calendario**, donde puedes ver y editar tus eventos a mano y suscribirte desde el móvil.

La primera respuesta tarda más porque el modelo se carga en memoria. Después, una petición al calendario tarda unos segundos.

Para pararlo: `docker compose down` (no borra tus datos, que están en `data/`).

## Documentación

| Documento | Para qué |
|---|---|
| [Instalación y uso](Docs/instalacion-y-uso.md) | Guía completa: desarrollo sin Docker, configuración, copias de seguridad, problemas frecuentes |
| [Crear un módulo](Docs/crear-un-modulo.md) | Cómo añadir capacidades nuevas a Mortimer |
| [Arquitectura y decisiones](Docs/arquitectura.md) | Por qué está hecho así |
| [Módulo calendario](modules/calendario/README.md) | Herramientas, página web, datos |

## Estructura

| Carpeta | Qué es |
|---|---|
| `core/` | Núcleo: chat, elección de herramientas, registro de módulos y de conversaciones |
| `modules/` | Módulos. `prueba` es la plantilla mínima; `calendario`, un módulo completo |
| `Docs/` | Documentación |
| `benchmarks/` | Comparativa de modelos locales en castellano y banco de evaluación |
| `scripts/` | Arranque en desarrollo, pruebas de humo y herramientas de evaluación |

## Seguridad

El chat no tiene usuario ni contraseña: cualquiera en tu red puede usarlo. **No abras el puerto 8000 a internet.** Para usarlo fuera de casa, conéctate por VPN.

---

Mortimer forma parte de un Trabajo Fin de Máster.

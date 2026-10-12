#!/usr/bin/env bash
# Arranca el sistema en local sin Docker (desarrollo). Requiere Ollama en marcha.
#   ./scripts/dev.sh            → core en :8000 + módulo de prueba en :8001
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r core/requirements-dev.txt -r modules/prueba/requirements.txt
fi

curl -sf "${OLLAMA_URL:-http://localhost:11434}/api/version" >/dev/null \
  || { echo "Ollama no responde. Ejecuta 'ollama serve' en otra terminal."; exit 1; }

trap 'kill 0' EXIT
(cd modules/prueba && ../../.venv/bin/uvicorn app.main:app --port 8001) &
sleep 2
(cd core && MODULOS=http://localhost:8001 DATA_DIR=../data/core ../.venv/bin/uvicorn app.main:app --port 8000 --reload) &
echo "Chat en http://localhost:8000  ·  módulo de prueba en http://localhost:8001/ui"
wait

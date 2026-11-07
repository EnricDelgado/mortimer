#!/usr/bin/env bash
# Prueba de humo contra un core en marcha.  Uso: ./scripts/smoke.sh [http://host:puerto]
# Solo comprueba que todo funciona; el rendimiento se mide con la regresión
# (benchmarks/benchmark_modelos.py) y la evaluación (benchmarks/evaluacion/ejecutar.py).
# Necesita curl y python3 (para leer el JSON de /api/modulos).
set -uo pipefail
BASE="${1:-http://localhost:8000}"
fallos=0
ok()  { printf '  \033[32m✓\033[0m %s\n' "$1"; }
ko()  { printf '  \033[31m✗\033[0m %s\n' "$1"; fallos=$((fallos+1)); }
chat() { curl -sf -m 120 "$BASE/api/chat" -H 'Content-Type: application/json' -d "{\"texto\":\"$1\"}"; }

echo "Smoke → $BASE"
h=$(curl -sf -m 10 "$BASE/health") && ok "health: $h" || { ko "health no responde"; exit 1; }

# Cada comprobación falla si la petición HTTP falla (antes daba verde con la
# respuesta vacía: QA D-01).
if m=$(curl -sf -m 10 "$BASE/api/modulos") && [ -n "$m" ]; then
  echo "$m" | grep -q '"estado":"error"' && ko "algún módulo caído: $m" || ok "módulos ok"
else
  m=""; ko "api/modulos no responde"
fi

# Calendario (Incremento 1): registrado y en estado ok.
estado_cal=$(echo "$m" | python3 -c '
import json, sys
try:
    mods = json.load(sys.stdin)
except Exception:
    print("sin-json"); sys.exit()
print(next((x.get("estado") for x in mods if x.get("nombre") == "calendario"), "ausente"))
' 2>/dev/null)
[ "$estado_cal" = "ok" ] && ok "módulo calendario registrado y ok" \
  || ko "módulo calendario: ${estado_cal:-sin respuesta}"

# El .ics suscribible llega a través del proxy del core (RF-06).
ics=$(curl -sf -m 10 "$BASE/m/calendario/calendario.ics" | head -c 15)
[ "$ics" = "BEGIN:VCALENDAR" ] && ok "calendario.ics por el proxy" \
  || ko "calendario.ics no empieza por BEGIN:VCALENDAR: '${ics}'"

r=$(chat "hola") && ok "conversación sin módulos" || ko "conversación sin módulos"

r=$(chat "¿qué hora es?")
echo "$r" | grep -q '"herramienta":"dime_la_hora"' && ok "enruta a dime_la_hora" \
  || ko "no enruta a dime_la_hora: $r"

t0=$(date +%s); r=$(chat "Explícame qué es el capitalismo de vigilancia"); rc=$?; t=$(( $(date +%s) - t0 ))
if [ $rc -ne 0 ] || ! echo "$r" | grep -q '"texto"'; then
  ko "conversación libre sin respuesta (curl=$rc): ${r:0:200}"
elif [ "$t" -le 30 ]; then
  ok "conversación libre en ${t}s (≤30s)"
else
  ko "conversación libre en ${t}s (>30s)"
fi

if i=$(curl -sf -m 10 "$BASE/api/interacciones?limite=4") && [ -n "$i" ]; then
  echo "$i" | grep -q '"error":"' && ko "hay errores en las últimas interacciones" || ok "interacciones sin errores"
else
  ko "api/interacciones no responde"
fi

[ "$fallos" -eq 0 ] && echo "OK" || { echo "$fallos fallo(s)"; exit 1; }

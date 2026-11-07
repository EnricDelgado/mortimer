#!/usr/bin/env bash
# Muestrea el consumo de CPU y RAM de los contenedores cada N segundos (evaluación,
# rendimiento en el equipo de referencia). Corre hasta que se le para (Ctrl+C o kill).
#
#   Uso: ./scripts/evaluacion/monitor_recursos.sh <segundos> <salida.csv>
#   Ej.: ./scripts/evaluacion/monitor_recursos.sh 5 /srv/mortimer-staging/eval/recursos.csv &
#        ...  (ejecutar el banco)
#        kill %1
#        python3 scripts/evaluacion/resumir.py recursos /srv/mortimer-staging/eval/recursos.csv
#
# Formato del CSV: marca,contenedor,cpu_pct,mem_uso,mem_pct
#   marca      instante de la muestra (ISO 8601, hora local del servidor)
#   cpu_pct    % de CPU según docker stats (100 % = un núcleo completo)
#   mem_uso    "usado / límite" tal como lo da docker stats (p. ej. 1.2GiB / 7.6GiB)
# Si el fichero ya existe, se añaden muestras al final (no se repite la cabecera).
set -uo pipefail

if [ $# -ne 2 ] || ! [[ "$1" =~ ^[0-9]+$ ]] || [ "$1" -lt 1 ]; then
  echo "Uso: $0 <segundos> <salida.csv>" >&2
  exit 2
fi
INTERVALO="$1"
SALIDA="$2"
command -v docker >/dev/null || { echo "docker no está instalado o no está en el PATH" >&2; exit 1; }

mkdir -p "$(dirname "$SALIDA")"
[ -s "$SALIDA" ] || echo "marca,contenedor,cpu_pct,mem_uso,mem_pct" > "$SALIDA"

seguir=1
trap 'seguir=0' INT TERM
echo "Muestreando cada ${INTERVALO}s en $SALIDA (Ctrl+C o kill para parar)" >&2
while [ "$seguir" -eq 1 ]; do
  marca=$(date '+%Y-%m-%dT%H:%M:%S')
  # --no-stream: una sola lectura. Los campos no contienen comas.
  docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}},{{.MemPerc}}' 2>/dev/null \
    | sed "s|^|${marca},|" >> "$SALIDA"
  # sleep en segundo plano + wait: la señal se atiende al momento.
  sleep "$INTERVALO" & wait $! 2>/dev/null || true
done
echo "Parado. Muestras en $SALIDA" >&2

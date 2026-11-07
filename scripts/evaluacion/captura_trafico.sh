#!/usr/bin/env bash
# Captura el tráfico que sale del servidor hacia fuera de la red de casa durante N
# minutos (RNF-01, privacidad: ningún dato sale a internet).
#
#   Uso: sudo ./scripts/evaluacion/captura_trafico.sh <minutos> <salida.pcap>
#   Ej.: sudo ./scripts/evaluacion/captura_trafico.sh 30 /srv/mortimer-staging/eval/trafico.pcap
#        (mientras tanto, repetir una pasada del banco)
#        tcpdump -nn -r /srv/mortimer-staging/eval/trafico.pcap > trafico.txt
#        python3 scripts/evaluacion/resumir.py trafico trafico.txt
#
# Qué se captura: la interfaz de salida del servidor (la de la ruta por defecto, o
# la que diga IFACE=...). El loopback y el tráfico entre contenedores (docker0 y
# las redes de compose) no pasan por ella. Se descarta el tráfico cuyos dos
# extremos son locales (10/8, 172.16/12, 192.168/16, 169.254/16, multicast y
# difusión; en IPv6, fe80::/10, fc00::/7 y ff00::/8), es decir, el uso normal del
# chat desde el móvil. Excepción: las consultas DNS (puerto 53) se capturan
# siempre, aunque vayan al router, porque delatan intentos de salir a internet.
set -uo pipefail

if [ $# -ne 2 ] || ! [[ "$1" =~ ^[0-9]+$ ]] || [ "$1" -lt 1 ]; then
  echo "Uso: sudo $0 <minutos> <salida.pcap>" >&2
  exit 2
fi
MINUTOS="$1"
SALIDA="$2"
command -v tcpdump >/dev/null || { echo "tcpdump no está instalado (sudo apt install tcpdump)" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || echo "Aviso: tcpdump suele necesitar sudo." >&2

IFACE="${IFACE:-$(ip route show default 2>/dev/null | awk '/default/ {for (i=1;i<=NF;i++) if ($i=="dev") {print $(i+1); exit}}')}"
[ -n "$IFACE" ] || { echo "No encuentro la interfaz de salida; indícala con IFACE=<interfaz>" >&2; exit 1; }

LOCAL4_SRC="(src net 10.0.0.0/8 or src net 172.16.0.0/12 or src net 192.168.0.0/16 or src net 169.254.0.0/16)"
LOCAL4_DST="(dst net 10.0.0.0/8 or dst net 172.16.0.0/12 or dst net 192.168.0.0/16 or dst net 169.254.0.0/16 or dst net 224.0.0.0/4 or dst host 255.255.255.255)"
LOCAL6_SRC="(src net fe80::/10 or src net fc00::/7)"
LOCAL6_DST="(dst net fe80::/10 or dst net fc00::/7 or dst net ff00::/8)"
FILTRO="(ip or ip6) and (port 53 or not ((ip and ${LOCAL4_SRC} and ${LOCAL4_DST}) or (ip6 and ${LOCAL6_SRC} and ${LOCAL6_DST})))"

mkdir -p "$(dirname "$SALIDA")"
echo "Capturando ${MINUTOS} min en ${IFACE} → ${SALIDA}" >&2
echo "Filtro: ${FILTRO}" >&2
timeout "$((MINUTOS * 60))" tcpdump -i "$IFACE" -nn -s 256 -w "$SALIDA" "$FILTRO"
rc=$?
# timeout devuelve 124 cuando se cumple el tiempo: es el final normal.
if [ "$rc" -eq 124 ] || [ "$rc" -eq 0 ]; then
  echo "Captura terminada: $SALIDA" >&2
  echo "Resumen: tcpdump -nn -r $SALIDA > trafico.txt && python3 scripts/evaluacion/resumir.py trafico trafico.txt" >&2
  exit 0
fi
exit "$rc"

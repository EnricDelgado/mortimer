#!/usr/bin/env python3
"""Resume las medidas de recursos y de tráfico de la evaluación (RNF-01, RNF-02 y rendimiento).

    python3 scripts/evaluacion/resumir.py recursos eval/recursos.csv
        CSV de monitor_recursos.sh → media y pico de CPU y RAM por contenedor,
        más el total de todos los contenedores en cada instante.

    python3 scripts/evaluacion/resumir.py trafico eval/trafico.txt
        Volcado de texto de `tcpdump -nn -r eval/trafico.pcap` → destinos externos
        (fuera de la red local) y consultas DNS. Acepta también el .pcap
        directamente si tcpdump está instalado.

Con --contenedores se filtran los contenedores por subcadena (p. ej. "staging").
La salida es Markdown, lista para pegar en el capítulo 4; con -o se guarda en un fichero.
Solo usa la biblioteca estándar.
"""

from __future__ import annotations

import argparse
import csv
import ipaddress
import re
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

# ── Recursos ─────────────────────────────────────────────────────────────────

UNIDADES = {
    "b": 1, "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3, "tib": 1024 ** 4,
    "kb": 1000, "mb": 1000 ** 2, "gb": 1000 ** 3, "tb": 1000 ** 4,
}
RE_TAMANO = re.compile(r"^\s*([\d.]+)\s*([a-zA-Z]+)\s*$")


def a_bytes(texto: str) -> float:
    """'512.3MiB' → bytes. Acepta las unidades de docker stats."""
    m = RE_TAMANO.match(texto)
    if not m or m.group(2).lower() not in UNIDADES:
        raise ValueError(f"tamaño no reconocido: {texto!r}")
    return float(m.group(1)) * UNIDADES[m.group(2).lower()]


def a_pct(texto: str) -> float:
    return float(texto.strip().rstrip("%").strip() or "nan")


def leer_recursos(ruta: Path, filtro: str | None = None) -> list[dict]:
    muestras = []
    with open(ruta, encoding="utf-8", newline="") as f:
        for fila in csv.DictReader(f):
            if filtro and filtro not in fila["contenedor"]:
                continue
            try:
                usado = fila["mem_uso"].split("/")[0]
                muestras.append({
                    "marca": fila["marca"], "contenedor": fila["contenedor"],
                    "cpu": a_pct(fila["cpu_pct"]), "mem": a_bytes(usado),
                    "mem_pct": a_pct(fila["mem_pct"]),
                })
            except (ValueError, KeyError, AttributeError):
                continue   # líneas incompletas (p. ej. un contenedor arrancando: "--")
    return muestras


def resumir_recursos(muestras: list[dict]) -> dict[str, dict]:
    por: dict[str, list[dict]] = defaultdict(list)
    for m in muestras:
        por[m["contenedor"]].append(m)
    res = {}
    for nombre in sorted(por):
        ms = por[nombre]
        res[nombre] = {
            "muestras": len(ms),
            "cpu_media": statistics.mean(m["cpu"] for m in ms),
            "cpu_pico": max(m["cpu"] for m in ms),
            "mem_media_mib": statistics.mean(m["mem"] for m in ms) / 1024 ** 2,
            "mem_pico_mib": max(m["mem"] for m in ms) / 1024 ** 2,
            "mem_pct_pico": max(m["mem_pct"] for m in ms),
        }
    # Total de todos los contenedores en cada instante (lo que ocupa Mortimer entero).
    instantes: dict[str, list[dict]] = defaultdict(list)
    for m in muestras:
        instantes[m["marca"]].append(m)
    if instantes:
        cpu = [sum(x["cpu"] for x in v) for v in instantes.values()]
        mem = [sum(x["mem"] for x in v) for v in instantes.values()]
        res["TOTAL"] = {
            "muestras": len(instantes),
            "cpu_media": statistics.mean(cpu), "cpu_pico": max(cpu),
            "mem_media_mib": statistics.mean(mem) / 1024 ** 2, "mem_pico_mib": max(mem) / 1024 ** 2,
            "mem_pct_pico": max(sum(x["mem_pct"] for x in v) for v in instantes.values()),
        }
    return res


def md_recursos(res: dict[str, dict], ruta: str, desde: str = "", hasta: str = "") -> str:
    lineas = [f"# Recursos · `{ruta}`", ""]
    if desde:
        lineas += [f"Muestras de {desde} a {hasta}. CPU: 100 % = un núcleo. RAM: memoria usada según docker stats.", ""]
    lineas += ["| Contenedor | Muestras | CPU media (%) | CPU pico (%) | RAM media (MiB) | RAM pico (MiB) | RAM pico (% del límite) |",
               "|---|---|---|---|---|---|---|"]
    for nombre, r in res.items():
        n = f"**{nombre}**" if nombre == "TOTAL" else nombre
        lineas.append(f"| {n} | {r['muestras']} | {r['cpu_media']:.1f} | {r['cpu_pico']:.1f} "
                      f"| {r['mem_media_mib']:.0f} | {r['mem_pico_mib']:.0f} | {r['mem_pct_pico']:.1f} |")
    if not res:
        lineas.append("| (sin muestras) | | | | | | |")
    return "\n".join(lineas) + "\n"


# ── Tráfico ──────────────────────────────────────────────────────────────────

# Las mismas redes que captura_trafico.sh considera locales. No se usa
# ipaddress.is_private porque incluye los rangos de documentación.
REDES_LOCALES = [ipaddress.ip_network(r) for r in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "127.0.0.0/8",
    "224.0.0.0/4", "255.255.255.255/32", "0.0.0.0/8",
    "::1/128", "::/128", "fe80::/10", "fc00::/7", "ff00::/8",
)]

RE_LINEA = re.compile(r"^(?P<hora>\S+(?:\s\d{2}:\d{2}:\d{2}\S*)?)\s.*?\bIP6?\s+(?P<src>\S+)\s+>\s+(?P<dst>\S+):(?:\s+(?P<resto>.*))?$")
RE_DNS = re.compile(r"\b(?:A|AAAA|PTR|HTTPS|SVCB|CNAME|MX|TXT|SRV|ANY)\?\s+(\S+?)\.?\s")


def separar(extremo: str) -> tuple[str, int | None]:
    """'192.168.1.2.443' → ('192.168.1.2', 443); '2001:db8::1.53' → ('2001:db8::1', 53)."""
    for candidato in (extremo, extremo.rsplit(".", 1)[0]):
        try:
            ipaddress.ip_address(candidato)
        except ValueError:
            continue
        if candidato == extremo:
            return candidato, None
        puerto = extremo.rsplit(".", 1)[1]
        return candidato, int(puerto) if puerto.isdigit() else None
    raise ValueError(extremo)


def es_local(ip: str) -> bool:
    d = ipaddress.ip_address(ip)
    return any(d in red for red in REDES_LOCALES if red.version == d.version)


def protocolo(resto: str, puertos: tuple) -> str:
    if "Flags [" in resto or resto.startswith("tcp"):
        return "TCP"
    if "ICMP" in resto:
        return "ICMP"
    if 53 in puertos:
        return "DNS"
    if "UDP" in resto or resto.startswith("udp") or any(p is not None for p in puertos):
        return "UDP"   # con puertos y sin flags TCP (NTP, QUIC, mDNS... los nombra tcpdump sin decir UDP)
    return "otro"


def leer_trafico(ruta: Path) -> list[str]:
    if ruta.suffix in (".pcap", ".pcapng"):
        r = subprocess.run(["tcpdump", "-nn", "-r", str(ruta)], capture_output=True, text=True)
        return r.stdout.splitlines()
    return ruta.read_text(encoding="utf-8", errors="replace").splitlines()


def resumir_trafico(lineas: list[str]) -> dict:
    destinos: dict[tuple, dict] = {}
    dns: dict[str, int] = defaultdict(int)
    paquetes = sin_formato = locales = 0
    for linea in lineas:
        if not linea.strip():
            continue
        m = RE_LINEA.match(linea)
        if not m:
            sin_formato += 1
            continue
        try:
            src, psrc = separar(m["src"])
            dst, pdst = separar(m["dst"])
        except ValueError:
            sin_formato += 1
            continue
        paquetes += 1
        resto = m["resto"] or ""
        q = RE_DNS.search(resto + " ")
        if q and pdst == 53:
            dns[q.group(1).rstrip(".")] += 1
        local_src, local_dst = es_local(src), es_local(dst)
        if local_src and local_dst:
            locales += 1
            continue
        # El extremo externo; si los dos lo son, se cuenta el destino.
        if not local_dst:
            externo, puerto, sentido = dst, pdst, "salida"
        else:
            externo, puerto, sentido = src, psrc, "entrada"
        clave = (externo, puerto, protocolo(resto, (psrc, pdst)))
        d = destinos.setdefault(clave, {"paquetes": 0, "salida": 0, "entrada": 0,
                                        "primero": m["hora"], "ultimo": m["hora"]})
        d["paquetes"] += 1
        d[sentido] += 1
        d["ultimo"] = m["hora"]
    return {"paquetes": paquetes, "locales": locales, "sin_formato": sin_formato,
            "destinos": destinos, "dns": dict(dns)}


def md_trafico(res: dict, ruta: str) -> str:
    d = res["destinos"]
    lineas = [f"# Tráfico · `{ruta}`", "",
              f"{res['paquetes']} paquetes analizados · {res['locales']} entre equipos locales "
              f"(DNS al router incluido) · {res['sin_formato']} líneas sin formato reconocido.", ""]
    if not d:
        lineas.append("**Ningún destino externo**: no sale tráfico del servidor fuera de la red local.")
    else:
        lineas += [f"**{len({k[0] for k in d})} destino(s) externo(s):**", "",
                   "| Destino | Puerto | Protocolo | Paquetes | Salida | Entrada | Primero | Último |",
                   "|---|---|---|---|---|---|---|---|"]
        for (ip, puerto, proto), v in sorted(d.items(), key=lambda kv: -kv[1]["paquetes"]):
            lineas.append(f"| {ip} | {puerto if puerto is not None else '–'} | {proto} | {v['paquetes']} "
                          f"| {v['salida']} | {v['entrada']} | {v['primero']} | {v['ultimo']} |")
    lineas += ["", "## Consultas DNS", ""]
    if res["dns"]:
        lineas += ["| Nombre | Consultas |", "|---|---|"]
        lineas += [f"| {n} | {c} |" for n, c in sorted(res["dns"].items(), key=lambda kv: (-kv[1], kv[0]))]
    else:
        lineas.append("Ninguna.")
    return "\n".join(lineas) + "\n"


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="orden", required=True)
    r = sub.add_parser("recursos", help="CSV de monitor_recursos.sh")
    r.add_argument("csv")
    r.add_argument("--contenedores", help="solo los contenedores cuyo nombre contenga esta subcadena")
    t = sub.add_parser("trafico", help="volcado de texto de tcpdump -nn -r (o el .pcap)")
    t.add_argument("fichero")
    for p in (r, t):
        p.add_argument("-o", "--salida", help="fichero Markdown de salida (por defecto, pantalla)")
    a = ap.parse_args(argv)

    if a.orden == "recursos":
        muestras = leer_recursos(Path(a.csv), a.contenedores)
        marcas = sorted(m["marca"] for m in muestras)
        texto = md_recursos(resumir_recursos(muestras), a.csv,
                            marcas[0] if marcas else "", marcas[-1] if marcas else "")
    else:
        texto = md_trafico(resumir_trafico(leer_trafico(Path(a.fichero))), a.fichero)

    if a.salida:
        Path(a.salida).write_text(texto, encoding="utf-8")
    else:
        sys.stdout.write(texto)
    return 0


if __name__ == "__main__":
    sys.exit(main())

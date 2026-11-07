"""Pruebas de DEV-13: scripts/evaluacion/resumir.py y sintaxis de los scripts de captura.

Los datos de tests/datos/ son inventados (direcciones de documentación
203.0.113.0/24, 198.51.100.0/24 y 2001:db8::/32), sin datos reales.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import resumir

DATOS = Path(__file__).resolve().parent / "datos"
SCRIPTS = Path(__file__).resolve().parents[3] / "scripts" / "evaluacion"


@pytest.mark.parametrize("texto,esperado", [
    ("512MiB", 512 * 1024 ** 2), ("1.5GiB", 1.5 * 1024 ** 3), ("200kB", 200_000),
    ("3GB", 3e9), ("10B", 10), ("7.66GiB ", 7.66 * 1024 ** 3),
])
def test_a_bytes(texto, esperado):
    assert resumir.a_bytes(texto) == pytest.approx(esperado)


def test_a_bytes_rechaza_basura():
    with pytest.raises(ValueError):
        resumir.a_bytes("--")


def test_recursos_media_y_pico_por_contenedor():
    res = resumir.resumir_recursos(resumir.leer_recursos(DATOS / "recursos_ejemplo.csv"))
    ollama = res["mortimer-ollama-1"]
    assert ollama["muestras"] == 3
    assert ollama["cpu_media"] == pytest.approx(200.0)
    assert ollama["cpu_pico"] == pytest.approx(390.0)
    assert ollama["mem_media_mib"] == pytest.approx(3.25 * 1024)
    assert ollama["mem_pico_mib"] == pytest.approx(3.5 * 1024)
    core = res["mortimer-staging-core-1"]
    assert core["muestras"] == 2                              # la línea con "--" se descarta
    assert core["mem_pico_mib"] == pytest.approx(140)
    total = res["TOTAL"]
    assert total["muestras"] == 3
    assert total["cpu_pico"] == pytest.approx(395.5)
    assert total["mem_pico_mib"] == pytest.approx(3.5 * 1024 + 140 + 54)


def test_recursos_filtro_y_markdown(tmp_path):
    salida = tmp_path / "r.md"
    assert resumir.main(["recursos", str(DATOS / "recursos_ejemplo.csv"),
                         "--contenedores", "staging", "-o", str(salida)]) == 0
    md = salida.read_text(encoding="utf-8")
    assert "mortimer-ollama-1" not in md
    assert "| mortimer-staging-calendario-stg-1 | 3 | 1.0 | 1.5 | 52 | 54 |" in md
    assert "**TOTAL**" in md and "2026-11-18T10:00:00" in md


def test_trafico_destinos_externos_y_dns():
    res = resumir.resumir_trafico(resumir.leer_trafico(DATOS / "trafico_ejemplo.txt"))
    destinos = res["destinos"]
    assert set(destinos) == {
        ("203.0.113.10", 443, "TCP"),
        ("198.51.100.7", 123, "UDP"),
        ("2001:db8:ffff::1", 443, "TCP"),
        ("198.51.100.99", None, "ICMP"),
    }
    https = destinos[("203.0.113.10", 443, "TCP")]
    assert https["paquetes"] == 3 and https["salida"] == 2 and https["entrada"] == 1
    assert res["dns"] == {"registry.ollama.ai": 1}
    assert res["locales"] == 3                       # DNS al router (ida y vuelta) y chat desde el móvil
    assert res["sin_formato"] == 2                    # cabecera de tcpdump y ARP


def test_trafico_markdown(capsys):
    assert resumir.main(["trafico", str(DATOS / "trafico_ejemplo.txt")]) == 0
    md = capsys.readouterr().out
    assert "**4 destino(s) externo(s):**" in md
    assert "| 203.0.113.10 | 443 | TCP | 3 | 2 | 1 |" in md
    assert "| registry.ollama.ai | 1 |" in md


def test_trafico_sin_salidas(tmp_path):
    f = tmp_path / "t.txt"
    f.write_text("10:00:00.000000 IP 192.168.1.30.52000 > 192.168.1.50.8000: Flags [P.], length 10\n",
                 encoding="utf-8")
    md = resumir.md_trafico(resumir.resumir_trafico(resumir.leer_trafico(f)), "t.txt")
    assert "**Ningún destino externo**" in md


def test_trafico_formato_tttt():
    linea = "2026-11-23 10:00:00.000000 IP 192.168.1.50.40000 > 198.51.100.1.443: Flags [S], length 0"
    res = resumir.resumir_trafico([linea])
    assert ("198.51.100.1", 443, "TCP") in res["destinos"]


@pytest.mark.parametrize("script", ["monitor_recursos.sh", "captura_trafico.sh"])
def test_scripts_sintaxis(script):
    r = subprocess.run(["bash", "-n", str(SCRIPTS / script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("script", ["monitor_recursos.sh", "captura_trafico.sh"])
def test_scripts_validan_argumentos(script):
    r = subprocess.run(["bash", str(SCRIPTS / script)], capture_output=True, text=True)
    assert r.returncode == 2 and "Uso:" in r.stderr


def test_monitor_con_docker_falso(tmp_path):
    """monitor_recursos.sh escribe la cabecera y una muestra por contenedor."""
    falso = tmp_path / "bin"
    falso.mkdir()
    (falso / "docker").write_text("#!/bin/sh\necho 'mortimer-core-1,1.50%,120MiB / 7.66GiB,1.53%'\n")
    (falso / "docker").chmod(0o755)
    salida = tmp_path / "eval" / "recursos.csv"
    import os
    env = {**os.environ, "PATH": f"{falso}:{os.environ['PATH']}"}
    try:
        subprocess.run(["bash", str(SCRIPTS / "monitor_recursos.sh"), "1", str(salida)],
                       env=env, capture_output=True, timeout=2.5)
    except subprocess.TimeoutExpired:
        pass
    lineas = salida.read_text(encoding="utf-8").splitlines()
    assert lineas[0] == "marca,contenedor,cpu_pct,mem_uso,mem_pct"
    assert len(lineas) >= 2 and lineas[1].endswith(",mortimer-core-1,1.50%,120MiB / 7.66GiB,1.53%")
    res = resumir.resumir_recursos(resumir.leer_recursos(salida))
    assert res["mortimer-core-1"]["mem_pico_mib"] == pytest.approx(120)

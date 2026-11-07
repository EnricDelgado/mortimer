"""Rutas de importación para las pruebas del banco: benchmarks/evaluacion
(ejecutar, metricas, banco), benchmarks (casos) y scripts/evaluacion (resumir).

Se ejecutan desde la raíz del repo con:
    (cd benchmarks && pytest -q evaluacion/tests)
"""

import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
EVALUACION = TESTS.parent
RAIZ = EVALUACION.parents[1]

for ruta in (EVALUACION, EVALUACION.parent, RAIZ / "scripts" / "evaluacion"):
    if str(ruta) not in sys.path:
        sys.path.insert(0, str(ruta))

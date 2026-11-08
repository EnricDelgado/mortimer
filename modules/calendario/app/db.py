"""Persistencia del módulo Calendario en SQLite.

SQLite es la fuente de verdad; el .ics se genera a partir de ella (ics.py).
Esquema de Docs/arquitectura (§4) con dos cambios del Incremento 1:
  - columna `todo_el_dia` (0/1) para los eventos sin hora;
  - no se crea `calendarios_externos`: RF-07 pasa a trabajo futuro.

Las fechas se guardan como texto ISO 8601 con zona en la hora local del
usuario ("2026-10-30T08:30:00+01:00"). Por eso las comparaciones de rango se
hacen sobre los 19 primeros caracteres (la hora local de pared), que ordenan
igual que el tiempo real salvo en la hora repetida del cambio de horario de
octubre, algo aceptable para una agenda personal.

Cada operación abre su propia conexión: el módulo atiende peticiones MCP y
HTTP desde hilos distintos y así no se comparte ninguna conexión entre hilos.
La base no se crea hasta la primera operación, de modo que importar el
módulo (por ejemplo, para listar sus herramientas) no toca el disco.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

ESQUEMA = """
CREATE TABLE IF NOT EXISTS eventos (
    id           TEXT PRIMARY KEY,        -- UUID, reutilizado como UID iCalendar
    titulo       TEXT NOT NULL,
    inicio       TEXT NOT NULL,           -- ISO 8601 con zona horaria
    fin          TEXT,
    todo_el_dia  INTEGER NOT NULL DEFAULT 0,
    ubicacion    TEXT,
    notas        TEXT,
    origen       TEXT,                    -- 'usuario' | 'asistente' | 'importado'
    creado_en    TEXT NOT NULL,
    cancelado_en TEXT                     -- borrado lógico: NULL = activo
);
CREATE INDEX IF NOT EXISTS eventos_inicio ON eventos (inicio);
"""

CAMPOS = ("id", "titulo", "inicio", "fin", "todo_el_dia", "ubicacion", "notas",
          "origen", "creado_en", "cancelado_en")
EDITABLES = ("titulo", "inicio", "fin", "todo_el_dia", "ubicacion", "notas")


def _clave(momento: str) -> str:
    """Normaliza un límite de rango a 'AAAA-MM-DDTHH:MM:SS' (hora local)."""
    m = str(momento)[:19]
    if len(m) == 10:
        return m + "T00:00:00"
    if len(m) == 16:
        return m + ":00"
    return m


class BaseDatos:
    def __init__(self, ruta: str | Path):
        self.ruta = Path(ruta)
        self._lista = False

    @contextmanager
    def _conexion(self) -> Iterator[sqlite3.Connection]:
        if not self._lista:
            self._crear()
        con = sqlite3.connect(self.ruta)
        con.row_factory = sqlite3.Row
        try:
            with con:  # transacción: commit al salir, rollback si hay excepción
                yield con
        finally:
            con.close()

    def _crear(self) -> None:
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.ruta)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(ESQUEMA)
            # Migración de bases creadas antes de existir `todo_el_dia`.
            columnas = {r[1] for r in con.execute("PRAGMA table_info(eventos)")}
            if "todo_el_dia" not in columnas:
                con.execute("ALTER TABLE eventos ADD COLUMN todo_el_dia INTEGER NOT NULL DEFAULT 0")
            con.commit()
        finally:
            con.close()
        self._lista = True

    @staticmethod
    def _a_dict(fila: sqlite3.Row | None) -> dict[str, Any] | None:
        if fila is None:
            return None
        d = {k: fila[k] for k in CAMPOS}
        d["todo_el_dia"] = bool(d["todo_el_dia"])
        return d

    # ── Operaciones ──────────────────────────────────────────────────────────

    _INSERT = f"INSERT INTO eventos ({', '.join(CAMPOS)}) VALUES ({', '.join(':' + k for k in CAMPOS)})"

    @staticmethod
    def _valores(evento: dict[str, Any]) -> dict[str, Any]:
        valores = {k: evento.get(k) for k in CAMPOS}
        valores["todo_el_dia"] = 1 if valores["todo_el_dia"] else 0
        return valores

    def insertar(self, evento: dict[str, Any]) -> dict[str, Any]:
        valores = self._valores(evento)
        with self._conexion() as con:
            con.execute(self._INSERT, valores)
        return self.obtener(valores["id"])

    def obtener(self, id: str) -> dict[str, Any] | None:
        with self._conexion() as con:
            fila = con.execute("SELECT * FROM eventos WHERE id = ?", (id,)).fetchone()
        return self._a_dict(fila)

    def listar(self, desde: str | None = None, hasta: str | None = None,
               incluir_cancelados: bool = False) -> list[dict[str, Any]]:
        """Eventos que se solapan con [desde, hasta), ordenados por inicio.

        `desde` y `hasta` son fechas u horas locales ('2026-10-30' o
        '2026-10-30T08:00'); `hasta` es exclusivo. Un evento sin `fin` se trata
        como un instante. Sin límites se devuelven todos.
        """
        condiciones, params = [], {}
        if not incluir_cancelados:
            condiciones.append("cancelado_en IS NULL")
        if hasta is not None:
            condiciones.append("substr(inicio, 1, 19) < :hasta")
            params["hasta"] = _clave(hasta)
        if desde is not None:
            condiciones.append(
                "(CASE WHEN fin IS NULL THEN substr(inicio, 1, 19) >= :desde"
                " ELSE substr(fin, 1, 19) > :desde END)"
            )
            params["desde"] = _clave(desde)
        where = ("WHERE " + " AND ".join(condiciones)) if condiciones else ""
        with self._conexion() as con:
            filas = con.execute(
                f"SELECT * FROM eventos {where} ORDER BY substr(inicio, 1, 19), titulo", params
            ).fetchall()
        return [self._a_dict(f) for f in filas]

    def actualizar(self, id: str, **campos: Any) -> dict[str, Any] | None:
        cambios = {k: v for k, v in campos.items() if k in EDITABLES}
        desconocidos = set(campos) - set(EDITABLES)
        if desconocidos:
            raise ValueError(f"Campos no editables: {', '.join(sorted(desconocidos))}")
        if "todo_el_dia" in cambios:
            cambios["todo_el_dia"] = 1 if cambios["todo_el_dia"] else 0
        if cambios:
            asignaciones = ", ".join(f"{k} = :{k}" for k in cambios)
            with self._conexion() as con:
                con.execute(f"UPDATE eventos SET {asignaciones} WHERE id = :id", {**cambios, "id": id})
        return self.obtener(id)

    def cancelar(self, id: str, cuando: str) -> dict[str, Any] | None:
        """Borrado lógico. Si ya estaba cancelado, conserva la fecha original."""
        with self._conexion() as con:
            con.execute(
                "UPDATE eventos SET cancelado_en = ? WHERE id = ? AND cancelado_en IS NULL",
                (cuando, id),
            )
        return self.obtener(id)

    def restaurar(self, id: str) -> dict[str, Any] | None:
        with self._conexion() as con:
            con.execute("UPDATE eventos SET cancelado_en = NULL WHERE id = ?", (id,))
        return self.obtener(id)

    def vaciar(self) -> None:
        with self._conexion() as con:
            con.execute("DELETE FROM eventos")

    def reemplazar(self, eventos: list[dict[str, Any]]) -> None:
        """Vacía la tabla y carga `eventos` en UNA transacción (reset de la evaluación).

        Si falla una inserción (p. ej. un id repetido) se deshace también el
        borrado, y ningún lector ve la agenda vacía o cargada a medias.
        """
        with self._conexion() as con:
            con.execute("DELETE FROM eventos")
            con.executemany(self._INSERT, [self._valores(e) for e in eventos])

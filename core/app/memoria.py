"""Persistencia del core: historial de conversaciones y registro de interacciones.

El registro de interacciones es el origen de los datos de la evaluación
(capítulo 3, fase 4): fecha, petición, módulo elegido, resultado y tiempo
de cada fase. Se guarda en SQLite para que se pueda consultar sin Mortimer.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ESQUEMA = """
CREATE TABLE IF NOT EXISTS mensajes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    conversacion  TEXT NOT NULL,
    rol           TEXT NOT NULL,          -- 'user' | 'assistant'
    contenido     TEXT NOT NULL,
    fecha         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_mensajes_conv ON mensajes(conversacion, id);

CREATE TABLE IF NOT EXISTS interacciones (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha          TEXT NOT NULL,         -- ISO 8601 UTC
    conversacion   TEXT NOT NULL,
    canal          TEXT NOT NULL,         -- 'web' | 'api' | 'telegram'
    modelo         TEXT NOT NULL,
    peticion       TEXT NOT NULL,
    respuesta      TEXT,
    modulo         TEXT,                  -- primer módulo invocado, NULL si ninguno
    herramientas   TEXT NOT NULL,         -- JSON: [{modulo, herramienta, argumentos, ok, ms}]
    rondas_llm     INTEGER NOT NULL,
    t_llm_ms       REAL NOT NULL,
    t_modulos_ms   REAL NOT NULL,
    t_total_ms     REAL NOT NULL,
    error          TEXT,
    truncada       INTEGER NOT NULL DEFAULT 0,  -- 1 si la respuesta se cortó por num_predict (DA-09)
    t_decision_ms  REAL,                        -- duración de la primera llamada al LLM (DEV-10)
    respuesta_modulo INTEGER NOT NULL DEFAULT 0, -- 1 si la respuesta la redactó el módulo (DA-11)
    despiertas     TEXT NOT NULL DEFAULT '[]',     -- JSON: módulos ofrecidos al modelo (DA-17)
    sin_capacidad  INTEGER NOT NULL DEFAULT 0      -- 1 si no había módulo para lo pedido (DA-17)
);

-- Lo que el usuario pidió y ningún módulo sabía hacer (DA-17, F1.4).
CREATE TABLE IF NOT EXISTS necesidades (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha          TEXT NOT NULL,
    conversacion   TEXT NOT NULL,
    interaccion_id INTEGER,
    peticion       TEXT NOT NULL,
    estado         TEXT NOT NULL DEFAULT 'abierta'  -- abierta | descartada | cubierta
);
"""

ESTADOS_NECESIDAD = ("abierta", "descartada", "cubierta")


class Memoria:
    def __init__(self, ruta: Path):
        ruta.parent.mkdir(parents=True, exist_ok=True)
        self.ruta = ruta
        self._db = sqlite3.connect(ruta, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        # Lo que se borra se sobrescribe con ceros: una conversación borrada no
        # queda legible en las páginas libres del fichero.
        self._db.execute("PRAGMA secure_delete = ON")
        self._db.executescript(ESQUEMA)
        self._migrar()

    def _migrar(self) -> None:
        # Bases creadas antes de DA-09 (Incremento 0) no tienen la columna.
        columnas = {f["name"] for f in self._db.execute("PRAGMA table_info(interacciones)")}
        nuevas = {"truncada": "INTEGER NOT NULL DEFAULT 0",          # DA-09
                  "t_decision_ms": "REAL",                           # DEV-10
                  "respuesta_modulo": "INTEGER NOT NULL DEFAULT 0",  # DEV-07 / DA-11
                  "despiertas": "TEXT NOT NULL DEFAULT '[]'",        # DA-17
                  "sin_capacidad": "INTEGER NOT NULL DEFAULT 0"}     # DA-17
        for nombre, tipo in nuevas.items():
            if nombre not in columnas:
                self._db.execute(f"ALTER TABLE interacciones ADD COLUMN {nombre} {tipo}")
        self._db.commit()

    @staticmethod
    def _ahora() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def historial(self, conversacion: str, limite: int) -> list[dict[str, str]]:
        filas = self._db.execute(
            "SELECT rol, contenido FROM mensajes WHERE conversacion=? ORDER BY id DESC LIMIT ?",
            (conversacion, limite),
        ).fetchall()
        return [{"role": f["rol"], "content": f["contenido"]} for f in reversed(filas)]

    def guardar_mensaje(self, conversacion: str, rol: str, contenido: str) -> None:
        self._db.execute(
            "INSERT INTO mensajes (conversacion, rol, contenido, fecha) VALUES (?,?,?,?)",
            (conversacion, rol, contenido, self._ahora()),
        )
        self._db.commit()

    def registrar(self, **campos: Any) -> int:
        campos.setdefault("fecha", self._ahora())
        campos["herramientas"] = json.dumps(campos.get("herramientas", []), ensure_ascii=False)
        if "despiertas" in campos:
            campos["despiertas"] = json.dumps(campos["despiertas"], ensure_ascii=False)
        columnas = ", ".join(campos)
        huecos = ", ".join("?" for _ in campos)
        cur = self._db.execute(
            f"INSERT INTO interacciones ({columnas}) VALUES ({huecos})", tuple(campos.values())
        )
        self._db.commit()
        return int(cur.lastrowid)

    def interacciones(self, limite: int = 50, desde_id: int | None = None) -> list[dict[str, Any]]:
        """Las últimas `limite` interacciones, de la más nueva a la más antigua.
        Con `desde_id`, solo las posteriores a ese id (lo usa ejecutar.py)."""
        filas = self._db.execute(
            "SELECT * FROM interacciones WHERE id > ? ORDER BY id DESC LIMIT ?",
            (desde_id or 0, limite),
        ).fetchall()
        res = []
        for f in filas:
            d = dict(f)
            d["herramientas"] = json.loads(d["herramientas"])
            d["despiertas"] = json.loads(d.get("despiertas") or "[]")
            res.append(d)
        return res

    # ── Necesidades no cubiertas (DA-17, F1.4) ────────────────────────────────

    def registrar_necesidad(self, conversacion: str, interaccion_id: int | None, peticion: str) -> int:
        cur = self._db.execute(
            "INSERT INTO necesidades (fecha, conversacion, interaccion_id, peticion) VALUES (?,?,?,?)",
            (self._ahora(), conversacion, interaccion_id, peticion),
        )
        self._db.commit()
        return int(cur.lastrowid)

    def necesidades(self, estado: str | None = None, limite: int = 200) -> list[dict[str, Any]]:
        if estado:
            filas = self._db.execute(
                "SELECT * FROM necesidades WHERE estado=? ORDER BY id DESC LIMIT ?", (estado, limite))
        else:
            filas = self._db.execute("SELECT * FROM necesidades ORDER BY id DESC LIMIT ?", (limite,))
        return [dict(f) for f in filas.fetchall()]

    def marcar_necesidad(self, nid: int, estado: str) -> bool:
        if estado not in ESTADOS_NECESIDAD:
            raise ValueError(f"estado desconocido: {estado}")
        cur = self._db.execute("UPDATE necesidades SET estado=? WHERE id=?", (estado, nid))
        self._db.commit()
        return cur.rowcount == 1

    # ── Registro de conversaciones (frontend del core) ────────────────────────
    # Lo que ve el usuario sale de `interacciones`: guarda la petición tal cual
    # la escribió (sin el sello de fecha) y las herramientas invocadas.

    def conversaciones(self, limite: int = 200) -> list[dict[str, Any]]:
        """Una fila por conversación, de la más reciente a la más antigua."""
        filas = self._db.execute(
            """
            SELECT conversacion,
                   COUNT(*)  AS turnos,
                   MIN(fecha) AS inicio,
                   MAX(fecha) AS ultima,
                   MAX(id)   AS ultimo_id,
                   (SELECT peticion FROM interacciones p
                     WHERE p.conversacion = i.conversacion ORDER BY id LIMIT 1) AS primera,
                   GROUP_CONCAT(DISTINCT canal) AS canales,
                   SUM(CASE WHEN modulo IS NOT NULL THEN 1 ELSE 0 END) AS con_modulo
              FROM interacciones i
             GROUP BY conversacion
             ORDER BY ultimo_id DESC
             LIMIT ?
            """,
            (limite,),
        ).fetchall()
        return [dict(f) for f in filas]

    def conversacion(self, conversacion: str) -> list[dict[str, Any]]:
        """Los turnos de una conversación, en orden. Lista vacía si no existe."""
        filas = self._db.execute(
            "SELECT id, fecha, canal, peticion, respuesta, modulo, herramientas, t_total_ms, "
            "error, truncada FROM interacciones WHERE conversacion=? ORDER BY id",
            (conversacion,),
        ).fetchall()
        res = []
        for f in filas:
            d = dict(f)
            d["herramientas"] = json.loads(d["herramientas"])
            res.append(d)
        return res

    def borrar_conversacion(self, conversacion: str) -> int:
        """Borra el historial y el registro de una conversación. Devuelve los turnos borrados.

        Se borra también de `interacciones`: contiene el texto de las peticiones,
        y el usuario tiene que poder eliminar lo que ha dicho (RNF de privacidad).
        """
        cur = self._db.execute("DELETE FROM interacciones WHERE conversacion=?", (conversacion,))
        self._db.execute("DELETE FROM mensajes WHERE conversacion=?", (conversacion,))
        self._db.execute("DELETE FROM necesidades WHERE conversacion=?", (conversacion,))
        self._db.commit()
        return cur.rowcount

    def borrar_todo(self) -> int:
        """Borra todas las conversaciones y su registro. Devuelve los turnos borrados."""
        cur = self._db.execute("DELETE FROM interacciones")
        self._db.execute("DELETE FROM mensajes")
        self._db.execute("DELETE FROM necesidades")
        self._db.commit()
        return cur.rowcount

    def uso_modulos(self) -> dict[str, dict[str, Any]]:
        """Por módulo: cuántas peticiones lo usaron y cuándo fue la última."""
        filas = self._db.execute(
            "SELECT modulo, COUNT(*) AS peticiones, MAX(fecha) AS ultima FROM interacciones "
            "WHERE modulo IS NOT NULL GROUP BY modulo"
        ).fetchall()
        return {f["modulo"]: {"peticiones": f["peticiones"], "ultima": f["ultima"]} for f in filas}

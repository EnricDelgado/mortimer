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
    error          TEXT
);
"""


class Memoria:
    def __init__(self, ruta: Path):
        ruta.parent.mkdir(parents=True, exist_ok=True)
        self.ruta = ruta
        self._db = sqlite3.connect(ruta, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(ESQUEMA)

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
        columnas = ", ".join(campos)
        huecos = ", ".join("?" for _ in campos)
        cur = self._db.execute(
            f"INSERT INTO interacciones ({columnas}) VALUES ({huecos})", tuple(campos.values())
        )
        self._db.commit()
        return int(cur.lastrowid)

    def interacciones(self, limite: int = 50) -> list[dict[str, Any]]:
        filas = self._db.execute(
            "SELECT * FROM interacciones ORDER BY id DESC LIMIT ?", (limite,)
        ).fetchall()
        res = []
        for f in filas:
            d = dict(f)
            d["herramientas"] = json.loads(d["herramientas"])
            res.append(d)
        return res

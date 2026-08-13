"""Accès SQLite. Rien de plus qu'une connexion correctement réglée."""

from __future__ import annotations

import pathlib
import sqlite3

from . import config

SCHEMA_PATH = pathlib.Path(__file__).with_name("schema.sql")


def connect(path: pathlib.Path | None = None, *, read_only: bool = False) -> sqlite3.Connection:
    target = path or config.DB_PATH
    if read_only:
        con = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(target)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    if not read_only:
        con.execute("PRAGMA journal_mode = WAL")
        # L'ingestion est un batch mono-écrivain : on peut se permettre de ne
        # pas fsync à chaque transaction, la base étant reconstructible.
        con.execute("PRAGMA synchronous = NORMAL")
    return con


def create_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def fts_available() -> bool:
    """FTS5 est une dépendance dure du résolveur — autant le dire tôt."""
    probe = sqlite3.connect(":memory:")
    try:
        probe.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        probe.close()

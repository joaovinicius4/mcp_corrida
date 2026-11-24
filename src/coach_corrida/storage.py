from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def encode(value) -> str:
    def convert(obj):
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        if isinstance(obj, bytes):
            return obj.hex()
        raise TypeError(f"Tipo não serializável: {type(obj).__name__}")
    return json.dumps(value, ensure_ascii=False, allow_nan=False, default=convert)


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_version VALUES(1);
CREATE TABLE IF NOT EXISTS sources(
 id INTEGER PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
 kind TEXT NOT NULL, stored_path TEXT NOT NULL, imported_at TEXT NOT NULL,
 report_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS activities(
 id INTEGER PRIMARY KEY, sport TEXT NOT NULL, local_date TEXT NOT NULL,
 started_at TEXT, distance_m REAL, moving_time_s REAL, name TEXT,
 created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS activities_date ON activities(local_date, sport);
CREATE TABLE IF NOT EXISTS observations(
 id INTEGER PRIMARY KEY, activity_id INTEGER NOT NULL REFERENCES activities(id),
 source_id INTEGER NOT NULL REFERENCES sources(id), record_key TEXT NOT NULL,
 priority INTEGER NOT NULL, facts_json TEXT NOT NULL,
 UNIQUE(source_id, record_key));
CREATE INDEX IF NOT EXISTS observations_activity ON observations(activity_id);
CREATE TABLE IF NOT EXISTS samples(
 activity_id INTEGER NOT NULL REFERENCES activities(id),
 source_id INTEGER NOT NULL REFERENCES sources(id), sequence INTEGER NOT NULL,
 timestamp TEXT NOT NULL, elapsed_s REAL NOT NULL, distance_m REAL,
 heart_rate REAL, speed_mps REAL, cadence REAL, altitude_m REAL,
 payload_json TEXT NOT NULL,
 PRIMARY KEY(source_id, activity_id, sequence));
CREATE TABLE IF NOT EXISTS laps(
 activity_id INTEGER NOT NULL REFERENCES activities(id),
 source_id INTEGER NOT NULL REFERENCES sources(id), sequence INTEGER NOT NULL,
 payload_json TEXT NOT NULL, PRIMARY KEY(source_id, activity_id, sequence));
CREATE TABLE IF NOT EXISTS source_rows(
 source_id INTEGER NOT NULL REFERENCES sources(id), sequence INTEGER NOT NULL,
 payload_json TEXT NOT NULL, PRIMARY KEY(source_id,sequence));
CREATE TABLE IF NOT EXISTS profile_entries(
 id INTEGER PRIMARY KEY, source_id INTEGER REFERENCES sources(id),
 entry_key TEXT NOT NULL, payload_json TEXT NOT NULL,
 UNIQUE(source_id,entry_key));
CREATE TABLE IF NOT EXISTS drafts(
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL, confirmed_at TEXT, saved_id INTEGER);
CREATE TABLE IF NOT EXISTS journal(
 id INTEGER PRIMARY KEY, activity_id INTEGER REFERENCES activities(id),
 date TEXT NOT NULL, payload_json TEXT NOT NULL, confirmed_at TEXT NOT NULL,
 draft_id TEXT UNIQUE NOT NULL REFERENCES drafts(id));
CREATE TABLE IF NOT EXISTS plans(
 id INTEGER PRIMARY KEY, week_start TEXT NOT NULL,
 version INTEGER NOT NULL, payload_json TEXT NOT NULL,
 approved_at TEXT NOT NULL, draft_id TEXT UNIQUE NOT NULL REFERENCES drafts(id),
 UNIQUE(week_start,version));
"""


class Store:
    def __init__(self, home: str | Path | None = None):
        self.home = Path(home or os.environ.get("COACH_DATA_DIR", "data")).resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        self.database_path = self.home / "coach.sqlite3"
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise ValueError("Banco de versão mais recente; não será alterado.")
            db.executescript(SCHEMA)
            db.execute("PRAGMA user_version=1")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database_path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def archive(self, path: Path) -> tuple[str, str]:
        path = path.resolve(strict=True)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        destination = self.home / "originals" / digest / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copy2(path, destination)
        if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
            raise ValueError("A cópia do original falhou na verificação de integridade.")
        return digest, str(destination.relative_to(self.home))

    def backup(self, destination: str | Path) -> dict:
        destination = Path(destination).resolve()
        if destination.exists():
            raise ValueError("O destino do backup já existe; escolha uma pasta nova.")
        if destination == self.home or self.home in destination.parents:
            raise ValueError("O backup deve ficar fora da pasta de dados.")
        destination.mkdir(parents=True)
        with self.connect() as db, sqlite3.connect(destination / "coach.sqlite3") as copy:
            db.backup(copy)
        if (self.home / "originals").exists():
            shutil.copytree(self.home / "originals", destination / "originals")
        return {"backup": str(destination), "includes": ["database", "originals"]}

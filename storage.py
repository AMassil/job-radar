"""Local SQLite journal. No CV, credentials or outgoing applications are stored/sent."""
from __future__ import annotations

from dataclasses import asdict
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from .core import Job, canonical_url, is_excluded_job
from .sources import CollectionResult, SourceReport

STATUSES = ("À étudier", "Favori", "À préparer", "Candidature envoyée", "Entretien", "Refus", "Écartée")
DEFAULT_DB = Path(__file__).resolve().parents[3] / ".job_radar" / "radar.sqlite3"


def job_key(job: Job) -> str:
    identity = canonical_url(job.url) or f"{job.company}|{job.title}|{job.location}"
    return hashlib.sha256(identity.encode()).hexdigest()


class RadarStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.getenv("JOB_RADAR_DB") or DEFAULT_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'À étudier', notes TEXT NOT NULL DEFAULT '',
                    follow_up TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
            ''')

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def save_collection(self, collection: CollectionResult, settings: dict) -> None:
        now = datetime.now(UTC).isoformat()
        snapshot = {"jobs": [asdict(j) for j in collection.jobs], "reports": [asdict(r) for r in collection.reports], "settings": settings}
        with self._connect() as db:
            for job in collection.jobs:
                db.execute('''INSERT INTO jobs(id,payload,first_seen,last_seen) VALUES (?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,last_seen=excluded.last_seen''',
                    (job_key(job), json.dumps(asdict(job), ensure_ascii=False), now, now))
            db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", ("latest", json.dumps(snapshot, ensure_ascii=False)))

    def load_collection(self) -> tuple[CollectionResult, dict] | None:
        payload = self.get_state("latest")
        if not payload:
            return None
        return CollectionResult(tuple(_job(j) for j in payload["jobs"]), tuple(SourceReport(**r) for r in payload["reports"])), payload["settings"]

    def get_state(self, key: str):
        with self._connect() as db:
            row = db.execute("SELECT payload FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_state(self, key: str, value) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))

    def records(self) -> dict[str, dict]:
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM jobs ORDER BY last_seen DESC").fetchall()
        records = {row["id"]: {**dict(row), "job": _job(json.loads(row["payload"]))} for row in rows}
        return {key: value for key, value in records.items() if not is_excluded_job(value["job"])}

    def update(self, job: Job, status: str, notes: str, follow_up: str = "") -> None:
        if status not in STATUSES:
            raise ValueError("Statut inconnu")
        if follow_up:
            datetime.strptime(follow_up, "%Y-%m-%d")
        now = datetime.now(UTC).isoformat()
        with self._connect() as db:
            db.execute('''INSERT INTO jobs(id,payload,first_seen,last_seen,status,notes,follow_up) VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET status=excluded.status,notes=excluded.notes,follow_up=excluded.follow_up''',
                (job_key(job), json.dumps(asdict(job), ensure_ascii=False), now, now, status, notes, follow_up))


def _job(payload: dict) -> Job:
    return Job(**{**payload, "tags": tuple(payload.get("tags", ()))})

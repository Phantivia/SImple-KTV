"""SQLite snapshots + immutable assets. Mutations are atomic and revision checked."""
from __future__ import annotations
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager
from typing import Callable


def uid() -> str:
    return uuid.uuid4().hex


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Conflict(Exception):
    pass


class Missing(Exception):
    pass


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.db() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, doc TEXT NOT NULL, cursor INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS history(project TEXT, seq INTEGER, doc TEXT, PRIMARY KEY(project,seq));
            CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY, project TEXT NOT NULL, doc TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, project TEXT NOT NULL, state TEXT NOT NULL, doc TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS jobs_project ON jobs(project, state);
            """)

    @contextmanager
    def db(self):
        con = sqlite3.connect(self.root / "ktv.sqlite", timeout=30)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def project_dir(self, project: str) -> Path:
        if len(project) != 32 or any(c not in "0123456789abcdef" for c in project):
            raise Missing("Project not found")
        return self.root / project

    def create(self, name: str) -> dict:
        p = {"id": uid(), "name": name[:100], "revision": 1, "created_at": now(), "updated_at": now(),
             "sample_rate": 48000, "duration": 0, "bpm": 100, "tonic": 0, "scale": "major", "key_shift": 0,
             "lyrics": [], "tracks": []}
        (self.project_dir(p["id"]) / "assets").mkdir(parents=True)
        with self.lock, self.db() as db:
            text = json.dumps(p)
            db.execute("INSERT INTO projects VALUES(?,?,?)", (p["id"], text, 0))
            db.execute("INSERT INTO history VALUES(?,?,?)", (p["id"], 0, text))
        return self.get(p["id"])

    def get(self, project: str) -> dict:
        with self.db() as db:
            row = db.execute("SELECT * FROM projects WHERE id=?", (project,)).fetchone()
            if not row:
                raise Missing("Project not found")
            p = json.loads(row["doc"])
            p["can_undo"] = row["cursor"] > 0
            p["can_redo"] = db.execute("SELECT 1 FROM history WHERE project=? AND seq=?", (project, row["cursor"] + 1)).fetchone() is not None
            return p

    def list(self) -> list[dict]:
        with self.db() as db:
            docs = [json.loads(r[0]) for r in db.execute("SELECT doc FROM projects")]
        return [{k: d[k] for k in ("id", "name", "updated_at", "duration", "revision")} for d in sorted(docs, key=lambda d: d["updated_at"], reverse=True)]

    def mutate(self, project: str, revision: int, change: Callable[[dict], None], *, from_job=False) -> dict:
        with self.lock, self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM projects WHERE id=?", (project,)).fetchone()
            if not row:
                raise Missing("Project not found")
            p = json.loads(row["doc"])
            if p["revision"] != revision:
                raise Conflict("Project changed in another tab. Reload and retry.")
            if not from_job and db.execute("SELECT 1 FROM jobs WHERE project=? AND state IN ('queued','running','cancelling')", (project,)).fetchone():
                raise Conflict("A job is using this project. Wait for completion or cancel it.")
            change(p)
            if len(p["tracks"]) > 64:
                raise ValueError("Maximum 64 tracks per project; remove unused takes before adding more.")
            p.pop("can_undo", None)
            p.pop("can_redo", None)
            p["revision"] += 1
            p["updated_at"] = now()
            p["duration"] = max([0.0] + [t["offset"] + t["duration"] for t in p["tracks"]])
            text = json.dumps(p, allow_nan=False)
            cursor = row["cursor"] + 1
            db.execute("DELETE FROM history WHERE project=? AND seq>=?", (project, cursor))
            db.execute("INSERT INTO history VALUES(?,?,?)", (project, cursor, text))
            db.execute("UPDATE projects SET doc=?,cursor=? WHERE id=?", (text, cursor, project))
        return self.get(project)

    def travel(self, project: str, revision: int, direction: int) -> dict:
        with self.lock, self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM projects WHERE id=?", (project,)).fetchone()
            if not row:
                raise Missing("Project not found")
            old = json.loads(row["doc"])
            if old["revision"] != revision:
                raise Conflict("Project changed. Reload and retry.")
            if db.execute("SELECT 1 FROM jobs WHERE project=? AND state IN ('queued','running','cancelling')", (project,)).fetchone():
                raise Conflict("Cancel the active job before undo/redo.")
            cursor = row["cursor"] + direction
            target = db.execute("SELECT doc FROM history WHERE project=? AND seq=?", (project, cursor)).fetchone()
            if not target:
                raise Conflict("Nothing to undo/redo")
            doc = json.loads(target[0])
            doc["revision"] = old["revision"] + 1
            doc["updated_at"] = now()
            db.execute("UPDATE projects SET doc=?,cursor=? WHERE id=?", (json.dumps(doc), cursor, project))
        return self.get(project)

    def add_asset(self, project: str, asset: dict):
        with self.db() as db:
            db.execute("INSERT INTO assets VALUES(?,?,?)", (asset["id"], project, json.dumps(asset, allow_nan=False)))

    def asset(self, project: str, asset_id: str) -> dict:
        with self.db() as db:
            row = db.execute("SELECT doc FROM assets WHERE id=? AND project=?", (asset_id, project)).fetchone()
        if not row:
            raise Missing("Audio asset not found")
        return json.loads(row[0])

    def asset_path(self, project: str, asset_id: str) -> Path:
        a = self.asset(project, asset_id)
        path = (self.project_dir(project) / "assets" / a["filename"]).resolve()
        if not path.is_relative_to(self.project_dir(project).resolve()) or not path.is_file():
            raise Missing("Audio file not found")
        return path

    def save_job(self, doc: dict):
        with self.lock, self.db() as db:
            db.execute("INSERT INTO jobs VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,doc=excluded.doc",
                       (doc["id"], doc["project"], doc["state"], json.dumps(doc, allow_nan=False)))

    def job(self, job_id: str) -> dict:
        with self.db() as db:
            r = db.execute("SELECT doc FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not r:
            raise Missing("Job not found")
        return json.loads(r[0])

    def jobs(self, project: str | None = None) -> list[dict]:
        with self.db() as db:
            rows = db.execute("SELECT doc FROM jobs WHERE project=? ORDER BY rowid DESC LIMIT 100", (project,)) if project else db.execute("SELECT doc FROM jobs")
            return [json.loads(r[0]) for r in rows]


def track_by_id(project: dict, track_id: str) -> dict:
    for t in project["tracks"]:
        if t["id"] == track_id:
            return t
    raise Missing("Track not found")


def new_track(asset: dict, name: str, role: str, *, offset=0.0, muted=False, gain_db=0.0, pan=0.0) -> dict:
    return {"id": uid(), "asset_id": asset["id"], "source_asset_id": asset["id"], "name": name[:100], "role": role,
            "duration": asset["duration"], "offset": offset, "gain_db": gain_db, "pan": pan, "muted": muted,
            "solo": False, "automation": [], "transpose": 0, "pitch": None}

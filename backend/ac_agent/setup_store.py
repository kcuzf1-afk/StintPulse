"""Persistence of the setup assistant (same SQLite file as the telemetry).

setup_versions     base setups (as read, never modified) and exported variants
setup_analyses     every analysis: inputs, key figures, what was sent, result
setup_assignments  MANUAL link "this session was driven with that version"
setup_specs        imported data/setup.ini of a car (when the game files are packed)
setup_confirmations  VALUE encodings the user confirmed from the in-game display
"""
from __future__ import annotations

import hashlib
import json
import uuid

from .laps import utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS setup_versions (
 id TEXT PRIMARY KEY, created_at TEXT NOT NULL, car TEXT NOT NULL, track TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('base','export')), parent_id TEXT, analysis_id TEXT,
 name TEXT NOT NULL, origin TEXT NOT NULL, content BLOB NOT NULL, sha256 TEXT NOT NULL,
 changes_json TEXT NOT NULL DEFAULT '[]', saved_path TEXT
);
CREATE INDEX IF NOT EXISTS setup_versions_by_car ON setup_versions(car,track,created_at);
CREATE TABLE IF NOT EXISTS setup_analyses (
 id TEXT PRIMARY KEY, created_at TEXT NOT NULL, car TEXT NOT NULL, track TEXT NOT NULL,
 base_version_id TEXT NOT NULL, lap_ids_json TEXT NOT NULL, goal TEXT NOT NULL,
 feedback_json TEXT NOT NULL, provider TEXT NOT NULL, model TEXT, status TEXT NOT NULL,
 kpis_json TEXT NOT NULL, sent_json TEXT, result_json TEXT NOT NULL, error TEXT
);
CREATE INDEX IF NOT EXISTS setup_analyses_by_car ON setup_analyses(car,track,created_at);
CREATE TABLE IF NOT EXISTS setup_assignments (
 session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
 version_id TEXT NOT NULL REFERENCES setup_versions(id) ON DELETE CASCADE,
 assigned_at TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS setup_specs (car TEXT PRIMARY KEY, content BLOB NOT NULL, imported_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS setup_confirmations (
 car TEXT NOT NULL, param TEXT NOT NULL, encoding TEXT NOT NULL, confirmed_at TEXT NOT NULL,
 PRIMARY KEY(car, param)
);
"""
TABLES = {"setup_versions", "setup_analyses", "setup_assignments", "setup_specs", "setup_confirmations"}


def version_row(row, content=False):
    out = {
        "id": row["id"],
        "created_at": row["created_at"],
        "car": row["car"],
        "track": row["track"],
        "kind": row["kind"],
        "parent_id": row["parent_id"],
        "analysis_id": row["analysis_id"],
        "name": row["name"],
        "origin": row["origin"],
        "sha256": row["sha256"],
        "changes": json.loads(row["changes_json"]),
        "saved_path": row["saved_path"],
    }
    if content:
        out["content"] = bytes(row["content"])
    return out


class SetupStore:
    def __init__(self, db):
        self.db = db
        with db.lock, db.conn:
            db.conn.executescript(SCHEMA)

    @property
    def conn(self):
        return self.db.conn

    # ---- versions
    def add_base(self, car, track, name, origin, content: bytes):
        digest = hashlib.sha256(content).hexdigest()
        with self.db.lock, self.conn:
            row = self.conn.execute(
                "SELECT * FROM setup_versions WHERE car=? AND track=? AND kind='base' AND sha256=? ORDER BY created_at LIMIT 1",
                (car, track, digest),
            ).fetchone()
            if row:
                return version_row(row)
            vid = str(uuid.uuid4())
            self.conn.execute(
                "INSERT INTO setup_versions(id,created_at,car,track,kind,name,origin,content,sha256) VALUES(?,?,?,?,?,?,?,?,?)",
                (vid, utc_now(), car, track, "base", name, origin, content, digest),
            )
        return self.version(vid)

    def add_export(self, car, track, parent_id, analysis_id, name, content: bytes, changes):
        vid = str(uuid.uuid4())
        with self.db.lock, self.conn:
            self.conn.execute(
                "INSERT INTO setup_versions(id,created_at,car,track,kind,parent_id,analysis_id,name,origin,content,sha256,changes_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    vid, utc_now(), car, track, "export", parent_id, analysis_id, name, "export",
                    content, hashlib.sha256(content).hexdigest(), json.dumps(changes, ensure_ascii=False),
                ),
            )
        return self.version(vid)

    def version(self, vid, content=False):
        with self.db.lock:
            row = self.conn.execute("SELECT * FROM setup_versions WHERE id=?", (vid,)).fetchone()
        return version_row(row, content) if row else None

    def versions(self, car, track):
        with self.db.lock:
            rows = self.conn.execute(
                "SELECT v.*, (SELECT COUNT(*) FROM setup_assignments a WHERE a.version_id=v.id) AS sessions FROM setup_versions v WHERE car=? AND track=? ORDER BY created_at DESC LIMIT 200",
                (car, track),
            ).fetchall()
        return [{**version_row(r), "sessions": r["sessions"]} for r in rows]

    def mark_saved(self, vid, path):
        with self.db.lock, self.conn:
            self.conn.execute("UPDATE setup_versions SET saved_path=? WHERE id=?", (str(path), vid))

    # ---- analyses
    def add_analysis(self, **a):
        aid = str(uuid.uuid4())
        with self.db.lock, self.conn:
            self.conn.execute(
                "INSERT INTO setup_analyses VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    aid, utc_now(), a["car"], a["track"], a["base_version_id"], json.dumps(a["lap_ids"]),
                    a["goal"], json.dumps(a["feedback"], ensure_ascii=False), a["provider"], a.get("model"),
                    a["status"], json.dumps(a["kpis"], ensure_ascii=False, allow_nan=False),
                    json.dumps(a["sent"], ensure_ascii=False) if a.get("sent") is not None else None,
                    json.dumps(a.get("result") or {}, ensure_ascii=False, allow_nan=False), a.get("error"),
                ),
            )
        return self.analysis(aid)

    def analysis(self, aid):
        with self.db.lock:
            r = self.conn.execute("SELECT * FROM setup_analyses WHERE id=?", (aid,)).fetchone()
        if not r:
            return None
        return {
            "id": r["id"],
            "created_at": r["created_at"],
            "car": r["car"],
            "track": r["track"],
            "base_version_id": r["base_version_id"],
            "lap_ids": json.loads(r["lap_ids_json"]),
            "goal": r["goal"],
            "feedback": json.loads(r["feedback_json"]),
            "provider": r["provider"],
            "model": r["model"],
            "status": r["status"],
            "kpis": json.loads(r["kpis_json"]),
            "sent": json.loads(r["sent_json"]) if r["sent_json"] else None,
            "result": json.loads(r["result_json"]),
            "error": r["error"],
        }

    def analyses(self, car, track):
        with self.db.lock:
            rows = self.conn.execute(
                "SELECT id FROM setup_analyses WHERE car=? AND track=? ORDER BY created_at DESC LIMIT 50", (car, track)
            ).fetchall()
        return [self.analysis(r[0]) for r in rows]

    # ---- manual assignment session -> version
    def assign(self, session_id, version_id, note=""):
        with self.db.lock, self.conn:
            if version_id is None:
                self.conn.execute("DELETE FROM setup_assignments WHERE session_id=?", (session_id,))
            else:
                self.conn.execute(
                    "INSERT OR REPLACE INTO setup_assignments VALUES(?,?,?,?)",
                    (session_id, version_id, utc_now(), note[:500]),
                )

    def assignments(self, version_ids=None):
        with self.db.lock:
            rows = self.conn.execute("SELECT * FROM setup_assignments").fetchall()
        return [
            {"session_id": r["session_id"], "version_id": r["version_id"], "assigned_at": r["assigned_at"], "note": r["note"], "manual": True}
            for r in rows
            if version_ids is None or r["version_id"] in version_ids
        ]

    # ---- car data
    def spec(self, car):
        with self.db.lock:
            row = self.conn.execute("SELECT content, imported_at FROM setup_specs WHERE car=?", (car,)).fetchone()
        return (bytes(row[0]), row[1]) if row else (None, None)

    def save_spec(self, car, content):
        with self.db.lock, self.conn:
            self.conn.execute("INSERT OR REPLACE INTO setup_specs VALUES(?,?,?)", (car, content, utc_now()))

    def delete_spec(self, car):
        with self.db.lock, self.conn:
            self.conn.execute("DELETE FROM setup_specs WHERE car=?", (car,))

    def confirmations(self, car):
        with self.db.lock:
            rows = self.conn.execute("SELECT param, encoding FROM setup_confirmations WHERE car=?", (car,)).fetchall()
        return {r[0]: r[1] for r in rows}

    def confirm(self, car, param, encoding):
        with self.db.lock, self.conn:
            if encoding is None:
                self.conn.execute("DELETE FROM setup_confirmations WHERE car=? AND param=?", (car, param))
            else:
                self.conn.execute(
                    "INSERT OR REPLACE INTO setup_confirmations VALUES(?,?,?,?)", (car, param, encoding, utc_now())
                )


def restore_tables(conn, backup, session_map, lap_map):
    """Merge setup tables of a validated backup (new ids, remapped sessions/laps)."""
    names = {r[0] for r in backup.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "setup_versions" not in names:
        return
    vmap = {r["id"]: str(uuid.uuid4()) for r in backup.execute("SELECT id FROM setup_versions")}
    amap = {r["id"]: str(uuid.uuid4()) for r in backup.execute("SELECT id FROM setup_analyses")} if "setup_analyses" in names else {}
    for r in backup.execute("SELECT * FROM setup_versions"):
        if r["kind"] not in ("base", "export"):
            raise ValueError("Invalid setup version")
        conn.execute(
            "INSERT INTO setup_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                vmap[r["id"]], r["created_at"], r["car"], r["track"], r["kind"], vmap.get(r["parent_id"]),
                amap.get(r["analysis_id"]), r["name"], r["origin"], bytes(r["content"]),
                hashlib.sha256(bytes(r["content"])).hexdigest(), r["changes_json"], None,
            ),
        )
    if amap:
        for r in backup.execute("SELECT * FROM setup_analyses"):
            json.loads(r["result_json"]), json.loads(r["kpis_json"])  # must parse
            laps = [lap_map[i] for i in json.loads(r["lap_ids_json"]) if i in lap_map]
            conn.execute(
                "INSERT INTO setup_analyses VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    amap[r["id"]], r["created_at"], r["car"], r["track"], vmap.get(r["base_version_id"], ""),
                    json.dumps(laps), r["goal"], r["feedback_json"], r["provider"], r["model"], r["status"],
                    r["kpis_json"], r["sent_json"], r["result_json"], r["error"],
                ),
            )
    if "setup_assignments" in names:
        for r in backup.execute("SELECT * FROM setup_assignments"):
            sid, vid = session_map.get(r["session_id"]), vmap.get(r["version_id"])
            if sid and vid:
                conn.execute("INSERT OR REPLACE INTO setup_assignments VALUES(?,?,?,?)", (sid, vid, r["assigned_at"], r["note"]))
    for table, sql in (
        ("setup_specs", "INSERT OR IGNORE INTO setup_specs VALUES(?,?,?)"),
        ("setup_confirmations", "INSERT OR IGNORE INTO setup_confirmations VALUES(?,?,?,?)"),
    ):
        if table in names:
            for r in backup.execute(f"SELECT * FROM {table}"):
                conn.execute(sql, tuple(r))

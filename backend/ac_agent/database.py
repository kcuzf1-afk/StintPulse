from __future__ import annotations
import json
import sqlite3
import threading
import uuid
import zlib
from pathlib import Path
import logging
from .models import ACCESS_CODE, Lap, SessionMeta, Settings, json_safe, new_access_code
from .laps import utc_now
from . import setup_store

log = logging.getLogger(__name__)


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS sessions (
 id TEXT PRIMARY KEY, created_at TEXT NOT NULL, ended_at TEXT,
 source TEXT NOT NULL CHECK(source IN ('ac','demo')), meta_json TEXT NOT NULL,
 favorite INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS laps (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 number INTEGER NOT NULL, duration_ms INTEGER NOT NULL, valid INTEGER NOT NULL,
 complete INTEGER NOT NULL, created_at TEXT NOT NULL, favorite INTEGER NOT NULL DEFAULT 0,
 summary_json TEXT NOT NULL, telemetry_zlib BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS laps_by_session ON laps(session_id,number);
CREATE INDEX IF NOT EXISTS laps_by_time ON laps(valid,duration_ms);
CREATE TABLE IF NOT EXISTS track_maps (key TEXT PRIMARY KEY, data_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), data_json TEXT NOT NULL);
-- Onboard video: one row per recording segment (a session may have several,
-- e.g. after the video source dropped out). Files live in the video folder.
CREATE TABLE IF NOT EXISTS recordings (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL, segment INTEGER NOT NULL,
 status TEXT NOT NULL, mime TEXT NOT NULL, path TEXT NOT NULL, created_at TEXT NOT NULL,
 started_at REAL, ended_at REAL, duration_ms INTEGER, bytes INTEGER NOT NULL DEFAULT 0,
 chunks INTEGER NOT NULL DEFAULT 0, indexed INTEGER NOT NULL DEFAULT 0,
 end_reason TEXT, error TEXT, info_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS recordings_by_session ON recordings(session_id,segment);
-- Lap <-> video timeline (server wall clock; see recordings.py).
CREATE TABLE IF NOT EXISTS lap_videos (
 lap_id TEXT PRIMARY KEY REFERENCES laps(id) ON DELETE CASCADE, session_id TEXT NOT NULL,
 recording_id TEXT, coverage TEXT NOT NULL, lap_start REAL, lap_end REAL,
 video_start_ms INTEGER, video_end_ms INTEGER, covered_ratio REAL, offset_s REAL,
 updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS lap_videos_by_session ON lap_videos(session_id);
PRAGMA user_version=1;
PRAGMA application_id=1094927687;
"""


VIDEO_TABLES = {"recordings", "lap_videos"}


def map_key(meta):
    return json.dumps([meta.source, meta.track, meta.layout], ensure_ascii=False)


def encode_samples(samples):
    """Compress bounded JSON batches without duplicating the entire lap in RAM."""
    compressor = zlib.compressobj(level=3)
    output = [compressor.compress(b"[")]
    for start in range(0, len(samples), 128):
        payload = ("," if start else "") + ",".join(
            sample.model_dump_json() for sample in samples[start : start + 128]
        )
        output.append(compressor.compress(payload.encode()))
    output.append(compressor.compress(b"]"))
    output.append(compressor.flush())
    return b"".join(output)


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=15)
        self.conn.row_factory = sqlite3.Row
        with self.conn:
            self.conn.executescript(SCHEMA)
            self.conn.executescript(setup_store.SCHEMA)

    def close(self):
        with self.lock:
            self.conn.close()

    def get_settings(self):
        with self.lock:
            row = self.conn.execute(
                "SELECT data_json FROM settings WHERE id=1"
            ).fetchone()
        data = json.loads(row[0]) if row else {}
        data.pop("ai_enabled", None)  # pre-0.2 placeholder, replaced by ai_provider
        # Access tokens were replaced by 8-digit codes: an old/invalid token is
        # reset to a fresh code; a missing one is created so the PC can show it.
        code = data.get("access_token") or ""
        if not ACCESS_CODE.fullmatch(code):
            data["access_token"] = new_access_code()
            settings = Settings.model_validate(data)
            self.save_settings(settings)
            log.warning(
                "LAN access code %s", "reset to a new 8-digit code" if code else "created"
            )
            return settings
        return Settings.model_validate(data)

    def save_settings(self, settings):
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO settings VALUES(1,?)",
                (settings.model_dump_json(),),
            )

    def new_session(self, meta, session_id=None, created_at=None):
        session_id = session_id or str(uuid.uuid4())
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO sessions(id,created_at,source,meta_json) VALUES(?,?,?,?)",
                (
                    session_id,
                    created_at or utc_now(),
                    meta.source,
                    meta.model_dump_json(),
                ),
            )
        return session_id

    def end_session(self, session_id):
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE sessions SET ended_at=? WHERE id=?", (utc_now(), session_id)
            )

    def session(self, session_id):
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
        if not row:
            return None
        return {
            **dict(row),
            "meta": json.loads(row["meta_json"]),
            "laps": self.list_laps(session_id),
        }

    def list_sessions(self, q="", source=None):
        with self.lock:
            rows = self.conn.execute(
                "SELECT s.*,COUNT(l.id) AS lap_count,MIN(CASE WHEN l.valid=1 THEN l.duration_ms END) AS best_ms,"
                "(SELECT COUNT(*) FROM recordings r WHERE r.session_id=s.id AND r.status!='failed') AS video_count FROM sessions s LEFT JOIN laps l ON l.session_id=s.id WHERE (?='' OR s.meta_json LIKE ?) AND (? IS NULL OR s.source=?) GROUP BY s.id ORDER BY s.created_at DESC LIMIT 500",
                (q, "%" + q + "%", source, source),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "created_at": r["created_at"],
                "ended_at": r["ended_at"],
                "meta": json.loads(r["meta_json"]),
                "favorite": bool(r["favorite"]),
                "lap_count": r["lap_count"],
                "best_ms": r["best_ms"],
                "video_count": r["video_count"],
            }
            for r in rows
        ]

    def save_lap(self, lap):
        summary = lap.model_dump(exclude={"samples"})
        data = encode_samples(lap.samples)
        with self.lock, self.conn:
            # One transaction per complete lap: no per-sample disk I/O.
            self.conn.execute(
                "INSERT INTO laps VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    lap.id,
                    lap.session_id,
                    lap.number,
                    lap.duration_ms,
                    int(lap.valid),
                    int(lap.complete),
                    lap.created_at,
                    int(lap.favorite),
                    json.dumps(
                        json_safe(summary), separators=(",", ":"), allow_nan=False
                    ),
                    data,
                ),
            )

    def lap(self, lap_id):
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM laps WHERE id=?", (lap_id,)
            ).fetchone()
        if not row:
            return None
        payload = json.loads(row["summary_json"])
        payload["samples"] = json.loads(zlib.decompress(row["telemetry_zlib"]))
        payload["favorite"] = bool(row["favorite"])
        return Lap.model_validate(payload)

    def list_laps(self, session_id):
        with self.lock:
            rows = self.conn.execute(
                "SELECT summary_json,favorite FROM laps WHERE session_id=? ORDER BY number,created_at",
                (session_id,),
            ).fetchall()
        return [{**json.loads(r[0]), "favorite": bool(r[1])} for r in rows]

    def session_laps(self, session_id):
        return [self.lap(row["id"]) for row in self.list_laps(session_id)]

    def best(self, meta):
        # Across sessions, only same driver/car/track/layout/source.
        with self.lock:
            rows = self.conn.execute(
                "SELECT l.id,s.meta_json FROM laps l JOIN sessions s ON s.id=l.session_id WHERE l.valid=1 AND l.complete=1 AND s.source=? ORDER BY l.duration_ms LIMIT 2000",
                (meta.source,),
            ).fetchall()
        for row in rows:
            other = SessionMeta.model_validate_json(row["meta_json"])
            if (other.driver, other.car, other.track, other.layout) == (
                meta.driver,
                meta.car,
                meta.track,
                meta.layout,
            ):
                return self.lap(row["id"])
        return None

    def best_sectors(self, meta):
        """Personal sector bests (ms) of valid, complete laps; same group as best()."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT l.summary_json,s.meta_json FROM laps l JOIN sessions s ON s.id=l.session_id WHERE l.valid=1 AND l.complete=1 AND s.source=?",
                (meta.source,),
            ).fetchall()
        best = [None] * meta.sector_count
        key = (meta.driver, meta.car, meta.track, meta.layout)
        for row in rows:
            other = SessionMeta.model_validate_json(row["meta_json"])
            if (other.driver, other.car, other.track, other.layout) != key:
                continue
            sectors = json.loads(row["summary_json"]).get("sectors_ms") or []
            if len(sectors) != meta.sector_count:
                continue  # different sector layout: not comparable
            for i, value in enumerate(sectors):
                if value is not None and (best[i] is None or value < best[i]):
                    best[i] = value
        return best

    def save_map(self, meta, track):
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO track_maps VALUES(?,?)",
                (map_key(meta), json.dumps(track, allow_nan=False)),
            )

    def map_candidates(self, meta, limit=40):
        """Newest stored laps of the same source/track/layout (any car/validity)."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT l.id,s.meta_json FROM laps l JOIN sessions s ON s.id=l.session_id WHERE s.source=? ORDER BY l.created_at DESC LIMIT 2000",
                (meta.source,),
            ).fetchall()
        ids = []
        for row in rows:
            other = SessionMeta.model_validate_json(row["meta_json"])
            if (other.track, other.layout) == (meta.track, meta.layout):
                ids.append(row["id"])
                if len(ids) >= limit:
                    break
        return ids

    def get_map(self, meta):
        with self.lock:
            row = self.conn.execute(
                "SELECT data_json FROM track_maps WHERE key=?", (map_key(meta),)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def favorite(self, session_id, value):
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE sessions SET favorite=? WHERE id=?", (int(value), session_id)
            )

    def delete_session(self, session_id):
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))

    def size_mb(self):
        return (
            sum(
                p.stat().st_size
                for p in self.path.parent.glob(self.path.name + "*")
                if p.is_file()
            )
            / 1024**2
        )

    def enforce_limit(
        self, mb, protected_session=None, reference_id=None, protected_sessions=()
    ):
        with self.lock:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

            def used_mb():
                # Deleted rows become free pages before VACUUM shrinks the file.
                # Looking only at file size here would delete every old session.
                pages = self.conn.execute("PRAGMA page_count").fetchone()[0]
                free = self.conn.execute("PRAGMA freelist_count").fetchone()[0]
                page_size = self.conn.execute("PRAGMA page_size").fetchone()[0]
                return (pages - free) * page_size / 1024**2

            rows = self.conn.execute(
                "SELECT s.id FROM sessions s WHERE s.favorite=0 AND s.id!=? AND NOT EXISTS(SELECT 1 FROM laps l WHERE l.session_id=s.id AND (l.favorite=1 OR l.id=?)) AND NOT EXISTS(SELECT 1 FROM recordings r WHERE r.session_id=s.id) ORDER BY s.created_at",
                (protected_session or "", reference_id or ""),
            ).fetchall()
            removed = []
            for row in rows:
                if row[0] in protected_sessions:
                    continue
                if used_mb() <= mb:
                    break
                self.delete_session(row[0])
                removed.append(row[0])
            if removed or (self.size_mb() > mb and used_mb() <= mb):
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self.conn.execute("VACUUM")
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            return {
                "removed": len(removed),
                "size_mb": round(self.size_mb(), 2),
                "limit_exceeded": self.size_mb() > mb,
            }

    def export_session(self, session_id):
        s = self.session(session_id)
        if not s:
            raise KeyError(session_id)
        return {
            "format": "ac-agent-session",
            "version": 1,
            "session": {
                "created_at": s["created_at"],
                "meta": s["meta"],
                "favorite": s["favorite"],
            },
            "laps": [l.model_dump() for l in self.session_laps(session_id)],
        }

    @staticmethod
    def validate_archive(payload):
        if payload.get("format") != "ac-agent-session" or payload.get("version") != 1:
            raise ValueError("Unsupported session format/version")
        meta = SessionMeta.model_validate(payload["session"]["meta"])
        laps = [Lap.model_validate(l) for l in payload["laps"]]
        if len(laps) > 500 or sum(len(l.samples) for l in laps) > 1000000:
            raise ValueError("Session exceeds import limits")
        for lap in laps:
            if len(lap.samples) < 2 or any(
                s.source != meta.source for s in lap.samples
            ):
                raise ValueError("Empty or mixed-source lap")
            if any(
                b.lap_ms < a.lap_ms or b.lap_pos + 0.001 < a.lap_pos
                for a, b in zip(lap.samples, lap.samples[1:])
            ):
                raise ValueError("Non-monotonic imported lap")
        return meta, laps

    def import_session(self, payload):
        meta, laps = self.validate_archive(payload)  # validate ALL before writing
        with self.lock, self.conn:
            sid = str(uuid.uuid4())
            self.conn.execute(
                "INSERT INTO sessions(id,created_at,source,meta_json,favorite) VALUES(?,?,?,?,?)",
                (
                    sid,
                    payload["session"]["created_at"],
                    meta.source,
                    meta.model_dump_json(),
                    int(payload["session"].get("favorite", False)),
                ),
            )
            remap = {l.id: str(uuid.uuid4()) for l in laps}
            for lap in laps:
                lap.id, lap.session_id = remap[lap.id], sid
                for tip in lap.tips:
                    if "reference_id" in tip:
                        tip["reference_id"] = remap.get(tip["reference_id"])
                # Inline write avoids nested transaction commits during import.
                data = encode_samples(lap.samples)
                self.conn.execute(
                    "INSERT INTO laps VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        lap.id,
                        sid,
                        lap.number,
                        lap.duration_ms,
                        int(lap.valid),
                        int(lap.complete),
                        lap.created_at,
                        int(lap.favorite),
                        lap.model_dump_json(exclude={"samples"}),
                        data,
                    ),
                )
        return sid

    def backup_to(self, path):
        """Complete SQLite snapshot, including maps; excludes settings and tokens."""
        target = sqlite3.connect(path)
        try:
            with self.lock:
                self.conn.backup(target)
            target.execute("PRAGMA secure_delete=ON")
            target.execute("DELETE FROM settings")
            # Videos are local files, not part of the database backup.
            target.execute("DROP TABLE IF EXISTS lap_videos")
            target.execute("DROP TABLE IF EXISTS recordings")
            target.commit()
            target.execute("VACUUM")
        finally:
            target.close()

    def restore_sqlite(self, path):
        """Merge a validated snapshot in one transaction, preserving existing data."""
        backup = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
        backup.row_factory = sqlite3.Row
        try:
            backup.execute("PRAGMA trusted_schema=OFF")
            objects = backup.execute(
                "SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if any(row["type"] not in ("table", "index") for row in objects):
                raise ValueError(
                    "Backup must not contain views, triggers or virtual tables"
                )
            tables = {row["name"] for row in objects if row["type"] == "table"}
            tables -= VIDEO_TABLES  # never restored: they point to local files
            if (
                tables - setup_store.TABLES != {"sessions", "laps", "track_maps", "settings"}
                or backup.execute("PRAGMA application_id").fetchone()[0] != 1094927687
                or backup.execute("PRAGMA user_version").fetchone()[0] != 1
            ):
                raise ValueError("Unsupported backup schema")
            if (
                backup.execute("PRAGMA quick_check").fetchone()[0] != "ok"
                or backup.execute("PRAGMA foreign_key_check").fetchone()
            ):
                raise ValueError("Backup integrity check failed")
            for table in tables:
                expected = [
                    r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")
                ]
                actual = [r[1] for r in backup.execute(f"PRAGMA table_info({table})")]
                if actual != expected:
                    raise ValueError("Backup columns do not match")
            sessions = backup.execute("SELECT * FROM sessions").fetchall()
            smap = {s["id"]: str(uuid.uuid4()) for s in sessions}
            lmap = {
                r[0]: str(uuid.uuid4()) for r in backup.execute("SELECT id FROM laps")
            }
            metas = {
                s["id"]: SessionMeta.model_validate_json(s["meta_json"])
                for s in sessions
            }
            with self.lock, self.conn:
                for s in sessions:
                    if s["source"] != metas[s["id"]].source:
                        raise ValueError("Mixed source session")
                    self.conn.execute(
                        "INSERT INTO sessions VALUES(?,?,?,?,?,?)",
                        (
                            smap[s["id"]],
                            s["created_at"],
                            s["ended_at"] or utc_now(),
                            s["source"],
                            metas[s["id"]].model_dump_json(),
                            int(bool(s["favorite"])),
                        ),
                    )
                for row in backup.execute("SELECT * FROM laps"):
                    blob = row["telemetry_zlib"]
                    decoder = zlib.decompressobj()
                    raw = decoder.decompress(blob, 192 * 1024**2 + 1)
                    if len(raw) > 192 * 1024**2 or not decoder.eof:
                        raise ValueError(
                            "Lap exceeds the 192 MiB expanded-telemetry limit"
                        )
                    summary = json.loads(row["summary_json"])
                    lap = Lap.model_validate({**summary, "samples": json.loads(raw)})
                    if (
                        lap.id != row["id"]
                        or lap.session_id != row["session_id"]
                        or lap.duration_ms != row["duration_ms"]
                        or lap.valid != bool(row["valid"])
                        or lap.complete != bool(row["complete"])
                    ):
                        raise ValueError("Inconsistent lap summary")
                    if len(lap.samples) < 2 or any(
                        s.source != metas[row["session_id"]].source for s in lap.samples
                    ):
                        raise ValueError("Invalid telemetry source")
                    if any(
                        b.lap_ms < a.lap_ms or b.lap_pos + 0.001 < a.lap_pos
                        for a, b in zip(lap.samples, lap.samples[1:])
                    ):
                        raise ValueError("Non-monotonic telemetry")
                    summary["id"], summary["session_id"] = (
                        lmap[row["id"]],
                        smap[row["session_id"]],
                    )
                    for tip in summary.get("tips", []):
                        if "reference_id" in tip:
                            tip["reference_id"] = lmap.get(tip["reference_id"])
                    self.conn.execute(
                        "INSERT INTO laps VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (
                            summary["id"],
                            summary["session_id"],
                            lap.number,
                            lap.duration_ms,
                            int(lap.valid),
                            int(lap.complete),
                            lap.created_at,
                            int(bool(row["favorite"])),
                            json.dumps(summary, allow_nan=False),
                            blob,
                        ),
                    )
                    del lap, raw
                setup_store.restore_tables(self.conn, backup, smap, lmap)
                for row in backup.execute("SELECT * FROM track_maps"):
                    key = json.loads(row["key"])
                    data = json.loads(row["data_json"])
                    if (
                        len(key) != 3
                        or key[0] not in ("ac", "demo")
                        or data.get("source") != key[0]
                    ):
                        raise ValueError("Invalid track-map source")
                    points = data.get("points", [])
                    import math

                    if len(points) > 2500 or any(
                        len(p) != 3
                        or not all(
                            isinstance(v, (int, float)) and math.isfinite(v) for v in p
                        )
                        or not 0 <= p[0] <= 1
                        for p in points
                    ):
                        raise ValueError("Invalid track geometry")
                    self.conn.execute(
                        "INSERT OR IGNORE INTO track_maps VALUES(?,?)",
                        (row["key"], json.dumps(data, allow_nan=False)),
                    )
            return list(smap.values())
        except sqlite3.DatabaseError as exc:
            raise ValueError("Invalid SQLite backup") from exc
        finally:
            backup.close()

import sqlite3
import threading
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS streamers (
 id INTEGER PRIMARY KEY, platform TEXT NOT NULL, room_id TEXT NOT NULL, name TEXT NOT NULL,
 monitor_enabled INTEGER NOT NULL DEFAULT 1, priority INTEGER NOT NULL DEFAULT 5,
 status TEXT NOT NULL DEFAULT 'offline', last_error TEXT, status_at TEXT, last_seen_at TEXT,
 UNIQUE(platform, room_id));
CREATE TABLE IF NOT EXISTS live_sessions (
 id INTEGER PRIMARY KEY, streamer_id INTEGER NOT NULL REFERENCES streamers(id),
 started_at TEXT NOT NULL, ended_at TEXT, stream_url TEXT);
CREATE TABLE IF NOT EXISTS match_code_observations (
 id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES live_sessions(id),
 match_code TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
 confirm_frames INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active');
CREATE TABLE IF NOT EXISTS collision_events (
 id INTEGER PRIMARY KEY, match_code TEXT NOT NULL, started_at TEXT NOT NULL,
 ended_at TEXT, status TEXT NOT NULL DEFAULT 'open');
CREATE TABLE IF NOT EXISTS collision_sessions (
 collision_id INTEGER NOT NULL REFERENCES collision_events(id),
 session_id INTEGER NOT NULL REFERENCES live_sessions(id), PRIMARY KEY(collision_id, session_id));
CREATE INDEX IF NOT EXISTS idx_obs_code ON match_code_observations(match_code, status);
CREATE INDEX IF NOT EXISTS idx_collision_status ON collision_events(status);
"""

class Database:
    def __init__(self, path):
        self.path = str(path)
        self._lock = threading.RLock()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()
    def execute(self, sql, params=()):
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur
    def close(self): self.conn.close()

    def _migrate(self):
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(streamers)")}
        for name, definition in {
            "status": "TEXT NOT NULL DEFAULT 'offline'",
            "last_error": "TEXT",
            "status_at": "TEXT",
            "last_seen_at": "TEXT",
        }.items():
            if name not in columns:
                self.conn.execute(f"ALTER TABLE streamers ADD COLUMN {name} {definition}")

    def set_streamer_status(self, streamer_id, status, error=None, at=None):
        self.execute(
            "UPDATE streamers SET status=?,last_error=?,status_at=? WHERE id=?",
            (status, error, at, streamer_id),
        )

    def touch_streamer(self, streamer_id, at):
        self.execute("UPDATE streamers SET last_seen_at=? WHERE id=?", (at, streamer_id))

    def seed_streamers(self, streamers):
        configured = {(s.platform, s.room_id) for s in streamers}
        if configured:
            placeholders = ",".join("(?,?)" for _ in configured)
            params = [value for pair in configured for value in pair]
            self.execute(
                f"""DELETE FROM streamers
                WHERE NOT EXISTS (SELECT 1 FROM live_sessions l WHERE l.streamer_id=streamers.id)
                AND (platform, room_id) NOT IN ({placeholders})""",
                params,
            )
            self.execute(
                f"UPDATE streamers SET monitor_enabled=0 WHERE (platform, room_id) NOT IN ({placeholders})",
                params,
            )
        for s in streamers:
            self.execute("""INSERT INTO streamers(platform,room_id,name,monitor_enabled,priority)
                         VALUES(?,?,?,?,?) ON CONFLICT(platform,room_id) DO UPDATE SET name=excluded.name,
                         monitor_enabled=excluded.monitor_enabled, priority=excluded.priority""",
                        (s.platform, s.room_id, s.name, int(s.monitor_enabled), s.priority))

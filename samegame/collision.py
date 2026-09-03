from datetime import datetime, timezone

def iso_now(): return datetime.now(timezone.utc).isoformat()

class CollisionService:
    def __init__(self, db, overlap_window_sec=300):
        self.db, self.window = db, overlap_window_sec
    def confirmed(self, session_id, code, at=None, frames=0):
        at = at or iso_now()
        active = self.db.execute("""SELECT * FROM match_code_observations
          WHERE session_id=? AND status='active' ORDER BY id DESC LIMIT 1""", (session_id,)).fetchone()
        if active and active["match_code"] == code:
            self.db.execute("UPDATE match_code_observations SET last_seen_at=?,confirm_frames=? WHERE id=?",
                            (at, max(frames, active["confirm_frames"]), active["id"]))
        else:
            if active: self.db.execute("UPDATE match_code_observations SET status='ended' WHERE id=?", (active["id"],))
            self.db.execute("""INSERT INTO match_code_observations
              (session_id,match_code,first_seen_at,last_seen_at,confirm_frames) VALUES(?,?,?,?,?)""",
                            (session_id, code, at, at, frames))
        self._collide(session_id, code, at)
    def end_code(self, session_id):
        self.db.execute("UPDATE match_code_observations SET status='ended' WHERE session_id=? AND status='active'", (session_id,))
    def _collide(self, session_id, code, at):
        rows = self.db.execute("""SELECT o.session_id, o.first_seen_at, l.streamer_id
          FROM match_code_observations o JOIN live_sessions l ON l.id=o.session_id
          WHERE o.match_code=? AND o.status='active'""", (code,)).fetchall()
        me = self.db.execute("SELECT streamer_id FROM live_sessions WHERE id=?",
                             (session_id,)).fetchone()
        my_streamer = me["streamer_id"] if me else None
        for other in rows:
            if other["session_id"] == session_id:
                continue
            # 同一直播间的多个 session（崩溃残留/断流重开/重启孤儿）永远不算撞车。
            if my_streamer is not None and other["streamer_id"] == my_streamer:
                continue
            try:
                delta = abs((datetime.fromisoformat(at) -
                             datetime.fromisoformat(other["first_seen_at"])).total_seconds())
            except ValueError:
                continue
            if delta > self.window:
                continue
            event = self.db.execute("""SELECT c.id FROM collision_events c JOIN collision_sessions cs ON c.id=cs.collision_id
              WHERE c.match_code=? AND cs.session_id=? AND c.status='open' LIMIT 1""", (code, session_id)).fetchone()
            if event: continue
            cur = self.db.execute("INSERT INTO collision_events(match_code,started_at) VALUES(?,?)", (code, at))
            cid = cur.lastrowid
            self.db.execute("INSERT OR IGNORE INTO collision_sessions VALUES(?,?)", (cid, session_id))
            self.db.execute("INSERT OR IGNORE INTO collision_sessions VALUES(?,?)", (cid, other["session_id"]))

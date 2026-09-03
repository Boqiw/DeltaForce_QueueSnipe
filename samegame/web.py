from flask import Flask, jsonify, render_template, request
from .collision import CollisionService

def create_app(db):
    app = Flask(__name__)
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    @app.get("/")
    def index(): return render_template("index.html")
    @app.get("/api/streamers")
    def streamers():
        return jsonify([dict(x) for x in db.execute("""SELECT s.*,
          (s.status='live' AND EXISTS(SELECT 1 FROM live_sessions l
           WHERE l.streamer_id=s.id AND l.ended_at IS NULL)) online,
          (SELECT o.match_code FROM match_code_observations o
           JOIN live_sessions l ON l.id=o.session_id
           WHERE l.streamer_id=s.id AND s.status='live' AND l.ended_at IS NULL AND o.status='active'
           ORDER BY o.id DESC LIMIT 1) current_match_code,
          (SELECT o.last_seen_at FROM match_code_observations o
           JOIN live_sessions l ON l.id=o.session_id
           WHERE l.streamer_id=s.id AND s.status='live' AND l.ended_at IS NULL AND o.status='active'
           ORDER BY o.id DESC LIMIT 1) current_match_code_seen_at
          FROM streamers s ORDER BY priority,name""")])
    @app.get("/api/collisions")
    def collisions():
        rows = db.execute("""SELECT c.*, group_concat(s.name, '、') names
          FROM collision_events c JOIN collision_sessions cs ON c.id=cs.collision_id
          JOIN live_sessions l ON l.id=cs.session_id JOIN streamers s ON s.id=l.streamer_id
          GROUP BY c.id ORDER BY c.started_at DESC LIMIT 100""")
        return jsonify([dict(x) for x in rows])
    @app.get("/api/materials")
    def materials(): return jsonify({"status": "deferred_to_v1", "items": []})

    @app.post("/api/observations")
    def observations():
      data = request.get_json(silent=True) or {}
      required = ("platform", "room_id", "name", "code")
      if any(not data.get(key) for key in required):
        return jsonify({"error": "platform, room_id, name and code are required"}), 400
      db.execute(
        """INSERT INTO streamers(platform,room_id,name) VALUES(?,?,?)
        ON CONFLICT(platform,room_id) DO UPDATE SET name=excluded.name""",
        (data["platform"], data["room_id"], data["name"]),
      )
      streamer = db.execute(
        "SELECT id FROM streamers WHERE platform=? AND room_id=?",
        (data["platform"], data["room_id"]),
      ).fetchone()
      session = db.execute(
        """SELECT id FROM live_sessions
        WHERE streamer_id=? AND ended_at IS NULL ORDER BY id DESC LIMIT 1""",
        (streamer["id"],),
      ).fetchone()
      if not session:
        session_id = db.execute(
          "INSERT INTO live_sessions(streamer_id,started_at,stream_url) VALUES(?,?,?)",
          (streamer["id"], data.get("at"), data.get("stream_url")),
        ).lastrowid
      else:
        session_id = session["id"]
      CollisionService(db).confirmed(session_id, data["code"], data.get("at"))
      return jsonify({"ok": True, "session_id": session_id, "code": data["code"]})
    return app

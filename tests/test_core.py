from samegame.config import Settings
from samegame.db import Database
from samegame.ocr import MatchCodeRecognizer
from samegame.collision import CollisionService
from samegame.web import create_app
from samegame.collector import StreamlinkResolver

def test_confirmation_and_normalization():
    r=MatchCodeRecognizer(r"^[A-Z0-9]{4,8}$", 3)
    assert r.observe("ZZ99") is None
    assert r.observe("AB12") is None
    assert r.observe("AB12") is None
    assert r.observe("AB12") == "AB12"

def test_douyin_cnuid_is_confirmed():
    r = MatchCodeRecognizer()
    text = "可领取 CNUID:186825715045264948544_202609022026"
    assert r.observe(text) is None
    assert r.observe(text) is None
    assert r.observe(text) == "CNUID186825715045264948544202609022026"

def test_douyin_cnuid_prefix_ocr_typos_are_normalized():
    r = MatchCodeRecognizer()
    text = "CN01D186825715045264948544_202609022058"
    assert r.normalize(text) == "CNUID186825715045264948544202609022058"

def test_douyin_cnuid_ignores_text_after_code():
    r = MatchCodeRecognizer()
    text = "CNU1D:186825715045264948544_2026090221006 6309048"
    assert r.normalize(text) == "CNUID186825715045264948544202609022100"

def test_douyin_follow_live_url_has_standard_candidate():
    url = "https://www.douyin.com/follow/live/944268231595?anchor_id=1760795425519690"
    assert StreamlinkResolver._candidate_urls("douyin", url)[1] == "https://live.douyin.com/944268231595"

def test_resolver_preserves_last_error():
    resolver = StreamlinkResolver()
    resolver.last_error = "No playable streams found"
    assert resolver.last_error == "No playable streams found"

def test_collision_is_persisted(tmp_path):
    db=Database(tmp_path/"x.sqlite3")
    a=db.execute("INSERT INTO streamers(platform,room_id,name) VALUES('x','1','A')").lastrowid
    b=db.execute("INSERT INTO streamers(platform,room_id,name) VALUES('x','2','B')").lastrowid
    sa=db.execute("INSERT INTO live_sessions(streamer_id,started_at) VALUES(?,?)",(a,"2026-01-01T00:00:00+00:00")).lastrowid
    sb=db.execute("INSERT INTO live_sessions(streamer_id,started_at) VALUES(?,?)",(b,"2026-01-01T00:00:01+00:00")).lastrowid
    c=CollisionService(db); c.confirmed(sa,"AB12","2026-01-01T00:01:00+00:00"); c.confirmed(sb,"AB12","2026-01-01T00:02:00+00:00")
    assert db.execute("SELECT count(*) n FROM collision_events").fetchone()["n"] == 1

def test_streamer_api_reports_only_active_match_code(tmp_path):
    db = Database(tmp_path / "x.sqlite3")
    streamer_id = db.execute(
        "INSERT INTO streamers(platform,room_id,name) VALUES('x','1','A')"
    ).lastrowid
    session_id = db.execute(
        "INSERT INTO live_sessions(streamer_id,started_at) VALUES(?,?)",
        (streamer_id, "2026-01-01T00:00:00+00:00"),
    ).lastrowid
    db.set_streamer_status(streamer_id, "live", at="2026-01-01T00:00:00+00:00")
    db.execute(
        """INSERT INTO match_code_observations
        (session_id,match_code,first_seen_at,last_seen_at,status)
        VALUES(?,?,?,?,?)""",
        (session_id, "AB12", "2026-01-01T00:01:00+00:00",
         "2026-01-01T00:02:00+00:00", "active"),
    )
    client = create_app(db).test_client()
    payload = client.get("/api/streamers").get_json()
    assert payload[0]["current_match_code"] == "AB12"

    db.execute(
        "UPDATE match_code_observations SET status='ended' WHERE session_id=?",
        (session_id,),
    )
    payload = client.get("/api/streamers").get_json()
    assert payload[0]["current_match_code"] is None

    db.set_streamer_status(streamer_id, "offline")
    db.execute(
        "UPDATE match_code_observations SET status='active' WHERE session_id=?",
        (session_id,),
    )
    payload = client.get("/api/streamers").get_json()
    assert payload[0]["current_match_code"] is None

def test_streamer_api_reports_monitor_status(tmp_path):
    db = Database(tmp_path / "x.sqlite3")
    streamer_id = db.execute(
        "INSERT INTO streamers(platform,room_id,name) VALUES('x','1','A')"
    ).lastrowid
    db.set_streamer_status(streamer_id, "reconnecting", "temporary failure", "2026-01-01T00:00:00+00:00")
    db.touch_streamer(streamer_id, "2026-01-01T00:00:01+00:00")
    payload = create_app(db).test_client().get("/api/streamers").get_json()
    assert payload[0]["status"] == "reconnecting"
    assert payload[0]["last_error"] == "temporary failure"
    assert payload[0]["last_seen_at"] == "2026-01-01T00:00:01+00:00"

    db.set_streamer_status(streamer_id, "offline")
    assert create_app(db).test_client().get("/api/streamers").get_json()[0]["online"] == 0

def test_seed_streamers_removes_unconfigured_empty_streamers(tmp_path):
    db = Database(tmp_path / "x.sqlite3")
    db.execute("INSERT INTO streamers(platform,room_id,name) VALUES('bilibili','example','example')")
    from samegame.config import StreamerConfig
    db.seed_streamers([StreamerConfig("douyin", "1", "A")])
    rows = db.execute("SELECT platform,room_id FROM streamers").fetchall()
    assert [(row["platform"], row["room_id"]) for row in rows] == [("douyin", "1")]

def test_config_uses_45_second_code_poll_interval(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[app]\ncode_poll_interval_sec = 45\n", encoding="utf-8")
    from samegame.config import load_config
    assert load_config(path).code_poll_interval_sec == 45

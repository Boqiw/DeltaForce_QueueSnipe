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

def test_douyin_cnuid_key_is_stable_prefix_without_timestamp():
    # 水印/对局码行 CN UID:<prefix>_<yyyymmddHHMM>，识别键只保留前段。
    r = MatchCodeRecognizer()
    text = "可领取 CNUID:186825715045264948544_202609022026"
    assert r.normalize(text) == "CNUID186825715045264948544"

def test_douyin_cnuid_key_same_across_minute_rollover():
    # 跨分钟翻转（...1400 -> ...1401）后键不变 —— 不再因时间戳变化中断确认。
    r = MatchCodeRecognizer(confirmation_frames=3)
    a = "CN UID:18598952304008742042_202609031400"
    b = "CN UID:18598952304008742042_202609031401"
    assert r.observe(a) is None
    assert r.observe(b) is None          # 换分钟仍算同一键，不被清空/判定不同
    assert r.observe(b) == "CNUID18598952304008742042"

def test_douyin_cnuid_one_frame_confirms():
    # 生产口径 confirmation_frames=1：单帧有效解析即确认。
    r = MatchCodeRecognizer(confirmation_frames=1)
    assert r.observe("CN UID:18598952304008742042_202609031400") == "CNUID18598952304008742042"

def test_douyin_cnuid_prefix_ocr_typos_are_normalized():
    r = MatchCodeRecognizer()
    text = "CN01D186825715045264948544_202609022058"
    assert r.normalize(text) == "CNUID186825715045264948544"

def test_douyin_cnuid_drops_noise_after_timestamp():
    # 时间戳后的 OCR 噪声应被丢掉，prefix 不受污染。
    r = MatchCodeRecognizer()
    text = "CNU1D:186825715045264948544_2026090221006 6309048"
    assert r.normalize(text) == "CNUID186825715045264948544"

def test_douyin_cnuid_requires_timestamp_boundary():
    # 没有可定位时间戳（无法区分 prefix 与噪声）视为未识别。
    r = MatchCodeRecognizer()
    assert r.normalize("CNUID18598952304008742042") is None
    assert r.normalize("CNUID1859895230400874204220260913") is None

def test_douyin_different_prefix_same_minute_are_different_keys():
    # 不同局/不同 prefix、同一分钟后缀相同（用户预警的陷阱）→ 键不同，不会误撞车。
    r = MatchCodeRecognizer()
    assert r.normalize("CNUID18598952304008742042_202609031400") != \
           r.normalize("CNUID18598952304008742099_202609031400")

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

def test_collision_hits_when_same_prefix_across_minutes(tmp_path):
    # A、B 同局：前段相同、分钟戳相差 1 分钟（1400/1401）→ 应判定撞车。
    db = Database(tmp_path / "x.sqlite3")
    a = db.execute("INSERT INTO streamers(platform,room_id,name) VALUES('x','1','A')").lastrowid
    b = db.execute("INSERT INTO streamers(platform,room_id,name) VALUES('x','2','B')").lastrowid
    sa = db.execute("INSERT INTO live_sessions(streamer_id,started_at) VALUES(?,?)",
                    (a, "2026-09-03T14:00:00+00:00")).lastrowid
    sb = db.execute("INSERT INTO live_sessions(streamer_id,started_at) VALUES(?,?)",
                    (b, "2026-09-03T14:00:10+00:00")).lastrowid
    r = MatchCodeRecognizer()
    key_a = r.normalize("CN UID:18598952304008742042_202609031400")
    key_b = r.normalize("CN UID:18598952304008742042_202609031401")
    assert key_a == key_b == "CNUID18598952304008742042"
    c = CollisionService(db)
    c.confirmed(sa, key_a, "2026-09-03T14:00:22+00:00")
    c.confirmed(sb, key_b, "2026-09-03T14:01:35+00:00")
    assert db.execute("SELECT count(*) n FROM collision_events").fetchone()["n"] == 1

def test_no_collision_when_same_minute_different_prefix(tmp_path):
    # A、B 不同局：同一分钟后缀相同（用户预警的"同分钟不同局后 12 位相同"陷阱）
    # 但前段不同 → 键不同，绝不误判撞车。
    db = Database(tmp_path / "x.sqlite3")
    a = db.execute("INSERT INTO streamers(platform,room_id,name) VALUES('x','1','A')").lastrowid
    b = db.execute("INSERT INTO streamers(platform,room_id,name) VALUES('x','2','B')").lastrowid
    sa = db.execute("INSERT INTO live_sessions(streamer_id,started_at) VALUES(?,?)",
                    (a, "2026-09-03T14:00:00+00:00")).lastrowid
    sb = db.execute("INSERT INTO live_sessions(streamer_id,started_at) VALUES(?,?)",
                    (b, "2026-09-03T14:00:10+00:00")).lastrowid
    r = MatchCodeRecognizer()
    key_a = r.normalize("CN UID:18598952304008742042_202609031400")
    key_b = r.normalize("CN UID:18598952304008742099_202609031400")
    assert key_a != key_b
    c = CollisionService(db)
    c.confirmed(sa, key_a, "2026-09-03T14:00:22+00:00")
    c.confirmed(sb, key_b, "2026-09-03T14:00:40+00:00")
    assert db.execute("SELECT count(*) n FROM collision_events").fetchone()["n"] == 0

def test_same_match_across_minutes_does_not_churn_observation(tmp_path):
    # 同一局跨分钟（1400/1401）只更新同一观测行，不再每分钟插新行。
    db = Database(tmp_path / "x.sqlite3")
    sid = db.execute("INSERT INTO streamers(platform,room_id,name) VALUES('x','1','A')").lastrowid
    session_id = db.execute("INSERT INTO live_sessions(streamer_id,started_at) VALUES(?,?)",
                            (sid, "2026-09-03T14:00:00+00:00")).lastrowid
    r = MatchCodeRecognizer()
    key = r.normalize("CN UID:18598952304008742042_202609031400")
    c = CollisionService(db)
    c.confirmed(session_id, key, "2026-09-03T14:00:22+00:00")
    c.confirmed(session_id, key, "2026-09-03T14:01:35+00:00")
    rows = db.execute("SELECT match_code,last_seen_at,status FROM match_code_observations "
                      "WHERE session_id=?", (session_id,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["match_code"] == key
    assert rows[0]["last_seen_at"] == "2026-09-03T14:01:35+00:00"
    assert rows[0]["status"] == "active"

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

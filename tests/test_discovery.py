"""板块自动发现相关单测（离线可跑，不碰浏览器/网络）。"""
import threading

from samegame.collector import BoardRoom
from samegame.config import Settings, StreamerConfig, load_config
from samegame.db import Database
from samegame.discovery import apply_round
from samegame.douyin_board import extract_rooms_from_json, parse_viewer_text
from samegame.monitor import LiveMonitor


def _row(db, room_id):
    return db.execute(
        "SELECT * FROM streamers WHERE platform='douyin' AND room_id=?", (room_id,)
    ).fetchone()


def test_apply_round_inserts_board_rooms_with_threshold(tmp_path):
    db = Database(tmp_path / "x.sqlite3")
    rooms = [
        BoardRoom("1", "主播A", 12000),   # >= 1万 -> 启用
        BoardRoom("2", "主播B", 9999),    # 低于阈值 -> 只登记不启用
        BoardRoom("3", "主播C", None),    # 无人气 -> 只登记不启用
    ]
    summary = apply_round(db, rooms, min_viewers=10000, miss_limit=2)
    assert summary.rooms == 3 and summary.added == 3 and summary.enabled == 1
    assert db.execute("SELECT count(*) n FROM streamers WHERE source='board'").fetchone()["n"] == 3
    assert _row(db, "1")["monitor_enabled"] == 1
    assert _row(db, "1")["viewer_count"] == 12000
    assert _row(db, "2")["monitor_enabled"] == 0
    assert _row(db, "3")["monitor_enabled"] == 0
    assert _row(db, "1")["miss_count"] == 0


def test_apply_round_updates_existing_instead_of_duplicating(tmp_path):
    db = Database(tmp_path / "x.sqlite3")
    apply_round(db, [BoardRoom("1", "旧名", 20000)], min_viewers=10000, miss_limit=2)
    summary = apply_round(db, [BoardRoom("1", "新名", 30000)], min_viewers=10000, miss_limit=2)
    assert summary.added == 0
    assert db.execute("SELECT count(*) n FROM streamers").fetchone()["n"] == 1
    row = _row(db, "1")
    assert row["name"] == "新名" and row["viewer_count"] == 30000
    assert row["monitor_enabled"] == 1


def test_apply_round_disables_after_miss_limit(tmp_path):
    db = Database(tmp_path / "x.sqlite3")
    apply_round(db, [BoardRoom("1", "主播A", 20000)], min_viewers=10000, miss_limit=2)
    # 第 1 轮不见：miss=1，仍保持启用
    summary = apply_round(db, [], min_viewers=10000, miss_limit=2)
    assert summary.missing == 1 and summary.disabled == 0
    assert _row(db, "1")["monitor_enabled"] == 1
    # 第 2 轮不见：达限停用
    summary = apply_round(db, [], min_viewers=10000, miss_limit=2)
    assert summary.disabled == 1 and summary.enabled == 0
    assert _row(db, "1")["monitor_enabled"] == 0
    assert _row(db, "1")["miss_count"] == 2


def test_apply_round_reappear_resets_miss_and_reenables(tmp_path):
    db = Database(tmp_path / "x.sqlite3")
    apply_round(db, [BoardRoom("1", "主播A", 20000)], min_viewers=10000, miss_limit=2)
    apply_round(db, [], min_viewers=10000, miss_limit=2)
    apply_round(db, [], min_viewers=10000, miss_limit=2)
    assert _row(db, "1")["monitor_enabled"] == 0
    # 主播回来且仍过万：清零 miss 并重新启用
    summary = apply_round(db, [BoardRoom("1", "主播A", 15000)], min_viewers=10000, miss_limit=2)
    assert _row(db, "1")["miss_count"] == 0
    assert _row(db, "1")["monitor_enabled"] == 1
    assert summary.enabled == 1


def test_apply_round_keeps_enabled_when_viewer_unknown(tmp_path):
    # 在播但本轮拿不到人气：不能因为缺数据误关已启用监控。
    db = Database(tmp_path / "x.sqlite3")
    apply_round(db, [BoardRoom("1", "主播A", 20000)], min_viewers=10000, miss_limit=2)
    summary = apply_round(db, [BoardRoom("1", "主播A", None)], min_viewers=10000, miss_limit=2)
    assert summary.enabled == 1
    assert _row(db, "1")["monitor_enabled"] == 1
    assert _row(db, "1")["miss_count"] == 0


def test_apply_round_never_touches_manual_rows(tmp_path):
    db = Database(tmp_path / "x.sqlite3")
    db.execute(
        """INSERT INTO streamers(platform,room_id,name,monitor_enabled,priority,source)
        VALUES('douyin','42','手工主播',1,5,'manual')""",
    )
    apply_round(db, [BoardRoom("42", "板块里别的名字", 7)], min_viewers=10000, miss_limit=2)
    row = _row(db, "42")
    assert row["source"] == "manual"
    assert row["name"] == "手工主播"          # 不覆盖名字/开关
    assert row["monitor_enabled"] == 1        # 低人气也不停用手工主播
    assert row["viewer_count"] == 7           # 人气照常更新，供面板对照
    # manual 行也不参与 miss 计停
    summary = apply_round(db, [], min_viewers=10000, miss_limit=2)
    assert summary.missing == 0
    assert _row(db, "42")["monitor_enabled"] == 1


def test_seed_streamers_keeps_board_rows(tmp_path):
    db = Database(tmp_path / "x.sqlite3")
    db.execute("INSERT INTO streamers(platform,room_id,name,source) VALUES('douyin','42','自动酱','board')")
    db.execute("INSERT INTO streamers(platform,room_id,name) VALUES('bilibili','9','旧手工')")
    db.seed_streamers([StreamerConfig("douyin", "1", "A")])
    rows = [(r["platform"], r["room_id"], r["source"]) for r in
            db.execute("SELECT platform,room_id,source FROM streamers ORDER BY room_id").fetchall()]
    assert rows == [("douyin", "1", "manual"), ("douyin", "42", "board")]


def test_config_discovery_defaults_and_parse(tmp_path):
    assert Settings().discovery.board_url == ""
    assert Settings().discovery.min_viewers == 10000
    path = tmp_path / "config.toml"
    path.write_text(
        "[discovery]\nboard_url = 'https://live.douyin.com/categorynew/x'\n"
        "min_viewers = 8000\ninterval_sec = 60\nscrolls = 3\nmiss_limit = 4\n",
        encoding="utf-8",
    )
    discovery = load_config(path).discovery
    assert discovery.board_url == "https://live.douyin.com/categorynew/x"
    assert discovery.min_viewers == 8000
    assert discovery.interval_sec == 60
    assert discovery.scrolls == 3
    assert discovery.miss_limit == 4


def test_parse_viewer_text():
    assert parse_viewer_text("1.2万") == 12000
    assert parse_viewer_text("1.5亿") == 150000000
    assert parse_viewer_text("3289") == 3289
    assert parse_viewer_text("3,200 人看过") == 3200
    assert parse_viewer_text("1.2万人在看") == 12000
    assert parse_viewer_text("开播提醒") is None
    assert parse_viewer_text("") is None
    assert parse_viewer_text(None) is None


def test_extract_rooms_from_json_various_shapes():
    # 房卡对象：web_rid + 同级 nickname + user_count
    payload = {"data": [{"web_rid": "123", "nickname": "A", "user_count": 12000}]}
    rooms = extract_rooms_from_json(payload)
    assert rooms == {"123": {"name": "A", "viewer_count": 12000}}
    # 昵称嵌在 user 子对象、room_id 为数字 int
    payload = {"data": [{"room_id": 456, "user": {"nickname": "B"}, "status": 2}]}
    rooms = extract_rooms_from_json(payload)
    assert rooms == {"456": {"name": "B", "viewer_count": None}}
    # 无关数字 id（id_str）不冒充房间
    payload = {"data": [{"id_str": "999", "nickname": "X"}]}
    assert extract_rooms_from_json(payload) == {}
    # 非纯数字 web_rid 忽略
    payload = {"data": [{"web_rid": "12ab", "nickname": "Y"}]}
    assert extract_rooms_from_json(payload) == {}
    # 多个房间去重保留
    payload = {"rooms": [{"web_rid": "1", "nickname": "一"},
                         {"web_rid": "1", "nickname": "一改"}]}
    assert extract_rooms_from_json(payload) == {"1": {"name": "一", "viewer_count": None}}


def test_monitor_run_forever_honors_preset_stop(tmp_path):
    # stop_event 已置位时 run_forever 应立即返回，不发解析/拉流请求。
    db = Database(tmp_path / "x.sqlite3")
    stop = threading.Event()
    stop.set()
    streamer = StreamerConfig("douyin", "1", "A")
    monitor = LiveMonitor(db, object(), Settings())
    monitor.run_forever(streamer, stop)
    assert stop.is_set()

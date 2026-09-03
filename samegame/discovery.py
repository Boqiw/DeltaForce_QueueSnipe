"""板块自动发现 + 高分主播同步服务。

职责：每轮调用 collector.fetch_board 拉板块在播房间，再用纯函数 apply_round
把结果落到 DB：
  * 全部在播房间登记为 source='board' 的 streamers 行（与 manual 行同
    platform+room_id 冲突时保留 manual 属性，不覆盖）；
  * viewer_count >= min_viewers 才启用监控（monitor_enabled=1）；本轮拿不到
    人气的已登记行只续期、不改开关，避免因单轮缺数据误关监控；
  * 本轮没出现的 board 行 miss_count+1，连续 miss_limit 轮不见才停用
    （monitor_enabled=0，保留历史行供面板对照）。

DB 里 manual 行（config 手工主播）不受 apply_round 影响；worker
（__main__.py）负责据此启停监控线程。
"""
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from .collector import fetch_board

log = logging.getLogger(__name__)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RoundSummary:
    rooms: int = 0      # 本轮看到的在播房间数（去重后）
    added: int = 0      # 新登记的 board 行数
    enabled: int = 0    # 当前 source='board' 且启用监控的行数
    missing: int = 0    # 本轮未见到的 board 行数
    disabled: int = 0   # 因连续 miss 达限而停用的行数

    def as_log(self) -> str:
        return (f"rooms={self.rooms} enabled={self.enabled} added={self.added} "
                f"missing={self.missing} disabled={self.disabled}")


def apply_round(db, rooms, min_viewers: int = 10000,
                miss_limit: int = 2, at: str | None = None) -> RoundSummary:
    """把一轮板块抓取结果同步进 DB，返回摘要。纯逻辑，离线可测。

    rooms 元素需有 room_id(str)/name(str)/viewer_count(int|None) 属性。
    manual 行完全跳过（但会顺手更新 viewer_count 供面板展示人气对照）。
    """
    at = at or now_utc()
    seen = set()
    added = 0
    for room in rooms:
        rid = str(room.room_id)
        if not rid.isdigit():
            continue
        seen.add(rid)
        name = (room.name or "").strip() or rid
        vc = room.viewer_count
        vc = int(vc) if isinstance(vc, (int, float)) and not isinstance(vc, bool) else None
        row = db.execute(
            "SELECT id,source FROM streamers WHERE platform='douyin' AND room_id=?",
            (rid,),
        ).fetchone()
        if row is None:
            want = 1 if vc is not None and vc >= min_viewers else 0
            db.execute(
                """INSERT INTO streamers
                   (platform,room_id,name,source,viewer_count,board_seen_at,miss_count,monitor_enabled)
                   VALUES('douyin',?,?,'board',?,?,0,?)""",
                (rid, name, vc, at, want),
            )
            added += 1
        elif row["source"] == "board":
            if vc is None:
                # 本轮在播但拿不到人气：不改开关，只续期登记、清零 miss。
                db.execute(
                    "UPDATE streamers SET name=?, board_seen_at=?, miss_count=0 WHERE id=?",
                    (name, at, row["id"]),
                )
            else:
                want = 1 if vc >= min_viewers else 0
                db.execute(
                    """UPDATE streamers SET name=?, viewer_count=?, board_seen_at=?,
                       miss_count=0, monitor_enabled=? WHERE id=?""",
                    (name, vc, at, want, row["id"]),
                )
        else:  # manual：保留 manual 属性，只把人气数据带上方便面板对照
            if vc is not None:
                db.execute(
                    "UPDATE streamers SET viewer_count=? WHERE id=?",
                    (vc, row["id"]),
                )

    missing = 0
    disabled = 0
    for row in db.execute(
        "SELECT id,room_id,miss_count,monitor_enabled FROM streamers WHERE source='board'"
    ).fetchall():
        if row["room_id"] in seen:
            continue
        missing += 1
        miss = (row["miss_count"] or 0) + 1
        if miss >= miss_limit:
            db.execute(
                "UPDATE streamers SET miss_count=?, monitor_enabled=0 WHERE id=?",
                (miss, row["id"]),
            )
            if row["monitor_enabled"]:
                disabled += 1
        else:
            db.execute(
                "UPDATE streamers SET miss_count=? WHERE id=?",
                (miss, row["id"]),
            )

    enabled = db.execute(
        "SELECT count(*) n FROM streamers WHERE source='board' AND monitor_enabled=1"
    ).fetchone()["n"]
    return RoundSummary(rooms=len(seen), added=added, enabled=enabled,
                        missing=missing, disabled=disabled)


class DiscoveryService:
    """daemon 循环：每 interval_sec 拉一轮板块并 apply_round。"""

    def __init__(self, db, settings):
        self.db, self.settings = db, settings

    def run_forever(self, stop_event: threading.Event | None = None) -> None:
        stop = stop_event or threading.Event()
        discovery = self.settings.discovery
        while not stop.is_set():
            if not discovery.board_url:
                log.info("discovery_idle_no_board_url")
                if stop.wait(60):
                    return
                continue
            try:
                result = fetch_board(discovery.board_url, scrolls=discovery.scrolls)
                if result.ok:
                    summary = apply_round(
                        self.db, result.rooms,
                        min_viewers=discovery.min_viewers,
                        miss_limit=discovery.miss_limit,
                    )
                    log.info("discovery_round", extra={"summary": summary.as_log()})
                else:
                    log.warning("discovery_round_failed",
                                extra={"error": result.error or "unknown"})
            except Exception:  # noqa: BLE001
                log.exception("discovery_round_crashed")
            if stop.wait(discovery.interval_sec):
                return

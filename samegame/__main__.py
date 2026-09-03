import argparse
from .config import load_config, StreamerConfig
from .db import Database
from .logsetup import configure
from .web import create_app
from .collector import StreamlinkResolver
from .discovery import DiscoveryService
from .monitor import LiveMonitor
import logging
import threading
import time

def main():
    p=argparse.ArgumentParser(); p.add_argument("--config", default="config.toml")
    args=p.parse_args(); cfg=load_config(args.config); configure(cfg.log_level)
    db=Database(cfg.database); db.seed_streamers(cfg.streamers)
    commands = {k: v.get("resolver_command", "") for k, v in cfg.platforms.items()}
    resolver = StreamlinkResolver(platform_commands=commands)

    def item_from_row(row):
        """优先取 config 手工项；否则（板块自动发现的主播）用 DB 行直接构造。
        douyin 板块房间没有独立 config 项，url 按 live.douyin.com/<room_id> 拼。"""
        item = next((x for x in cfg.streamers
                     if x.platform == row["platform"] and x.room_id == row["room_id"]), None)
        if item is None:
            item = StreamerConfig(
                platform=row["platform"], room_id=row["room_id"],
                name=row["name"] or row["room_id"],
                url=("https://live.douyin.com/%s" % row["room_id"])
                if row["platform"] == "douyin" else "",
                capture_source="stream", capture_input=None,
                monitor_enabled=bool(row["monitor_enabled"]), priority=row["priority"])
        item.id = row["id"]
        return item

    # Monitoring is deliberately isolated from Flask; a failed route cannot stop the panel.
    def worker():
        running = {}  # row_id -> (thread, stop_event)
        while True:
            rows = db.execute("SELECT * FROM streamers WHERE monitor_enabled=1 ORDER BY priority DESC").fetchall()
            next_running = {}
            for row in rows:
                rid = row["id"]
                entry = running.pop(rid, None)
                if entry and entry[0].is_alive():
                    # 沿用现线程（可能正在被 stop_event 要求退出，退完下轮重建）。
                    next_running[rid] = entry
                    continue
                if entry:
                    entry[1].set()
                item = item_from_row(row)
                stop_event = threading.Event()
                monitor = LiveMonitor(db, resolver, cfg)
                # 每路主播一个常驻线程：内部自带"解析→直播监控(断流同URL重开)
                # → 离线指数退避→重试"循环，不再由 worker 每 5 秒反复重启。
                thread = threading.Thread(
                    target=monitor.run_forever, args=(item, stop_event),
                    daemon=True, name=f"mon-{item.name}-{rid}")
                next_running[rid] = (thread, stop_event)
                thread.start()
            for rid, (thread, stop_event) in running.items():
                # 本轮不再启用的行（板块停用/被删）：请求线程尽快退出。
                if thread.is_alive():
                    stop_event.set()
                    db.execute("UPDATE streamers SET status='disabled', last_error=? WHERE id=?",
                               ("stopped by worker", rid))
            running = next_running
            if not rows:
                logging.getLogger(__name__).warning("no_enabled_streamers")
            time.sleep(5)
    threading.Thread(target=DiscoveryService(db, cfg).run_forever, daemon=True).start()
    threading.Thread(target=worker, daemon=True).start()
    create_app(db).run(host=cfg.host, port=cfg.port, debug=False)

if __name__ == "__main__": main()

import argparse
from .config import load_config
from .db import Database
from .logsetup import configure
from .web import create_app
from .collector import StreamlinkResolver
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
    # Monitoring is deliberately isolated from Flask; a failed route cannot stop the panel.
    def worker():
        running = {}
        while True:
            rows = db.execute("SELECT * FROM streamers WHERE monitor_enabled=1 ORDER BY priority DESC").fetchall()
            for row in rows:
                item = next((x for x in cfg.streamers if x.platform == row["platform"] and x.room_id == row["room_id"]), None)
                if item:
                    item.id = row["id"]
                    thread = running.get(row["id"])
                    if not thread or not thread.is_alive():
                        thread = threading.Thread(target=LiveMonitor(db, resolver, cfg).run_streamer,
                                                  args=(item,), daemon=True)
                        running[row["id"]] = thread
                        thread.start()
            if not rows:
                logging.getLogger(__name__).warning("no_enabled_streamers")
            time.sleep(5)
    threading.Thread(target=worker, daemon=True).start()
    create_app(db).run(host=cfg.host, port=cfg.port, debug=False)

if __name__ == "__main__": main()

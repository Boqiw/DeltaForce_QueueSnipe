from dataclasses import dataclass, field
from pathlib import Path
import re
import tomllib

@dataclass
class StreamerConfig:
    platform: str
    room_id: str
    name: str
    url: str = ""
    capture_source: str = "stream"
    capture_input: str | int | None = None
    monitor_enabled: bool = True
    priority: int = 5

@dataclass
class Settings:
    database: str = "samegame.sqlite3"
    host: str = "127.0.0.1"
    port: int = 5000
    sample_interval_sec: float = 2
    code_poll_interval_sec: float = 45
    confirmation_frames: int = 3
    overlap_window_sec: int = 300
    url_ttl_sec: float = 900
    reopen_max: int = 5
    reopen_interval_sec: float = 5
    offline_backoff_base_sec: float = 30
    offline_backoff_max_sec: float = 300
    stream_end_retry_sec: float = 15
    log_level: str = "INFO"
    roi: tuple[float, float, float, float] = (0.005, 0.085, 0.16, 0.035)
    pattern: str = r"^CNUID[0-9]{33}$"
    missing_alert_frames: int = 30
    streamers: list[StreamerConfig] = field(default_factory=list)
    platforms: dict = field(default_factory=dict)

def load_config(path: str | Path) -> Settings:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    app, ocr = raw.get("app", {}), raw.get("ocr", {})
    s = Settings(
        database=app.get("database", "samegame.sqlite3"), host=app.get("host", "127.0.0.1"),
        port=int(app.get("port", 5000)), sample_interval_sec=float(app.get("sample_interval_sec", 2)),
        code_poll_interval_sec=float(app.get("code_poll_interval_sec", 45)),
        confirmation_frames=int(app.get("confirmation_frames", 3)),
        overlap_window_sec=int(app.get("overlap_window_sec", 300)),
        url_ttl_sec=float(app.get("url_ttl_sec", 900)),
        reopen_max=int(app.get("reopen_max", 5)),
        reopen_interval_sec=float(app.get("reopen_interval_sec", 5)),
        offline_backoff_base_sec=float(app.get("offline_backoff_base_sec", 30)),
        offline_backoff_max_sec=float(app.get("offline_backoff_max_sec", 300)),
        stream_end_retry_sec=float(app.get("stream_end_retry_sec", 15)),
        log_level=app.get("log_level", "INFO"),
        roi=tuple(ocr.get("roi", [0.005, 0.085, 0.16, 0.035])), pattern=ocr.get("pattern", r"^CNUID[0-9]{33}$"),
        missing_alert_frames=int(ocr.get("missing_alert_frames", 30)),
        streamers=[StreamerConfig(**x) for x in raw.get("streamers", [])],
        platforms=raw.get("platforms", {}),
    )
    re.compile(s.pattern)
    return s

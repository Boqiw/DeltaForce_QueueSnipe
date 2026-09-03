import logging
import time
from datetime import datetime, timezone
from .collector import StreamResolver
from .collision import CollisionService
from .capture import open_capture
from .ocr import FrameOCR, MatchCodeRecognizer

log = logging.getLogger(__name__)

def now():
    return datetime.now(timezone.utc).isoformat()

class LiveMonitor:
    """Pulls frames for one streamer and feeds only OCR observations downstream."""
    def __init__(self, db, resolver: StreamResolver, settings):
        self.db, self.resolver, self.settings = db, resolver, settings
        self.collisions = CollisionService(db, settings.overlap_window_sec)

    def run_forever(self, streamer):
        """每路主播的常驻监控线程主体（在 __main__ 里每路启动一个，不退出）。

        循环：resolve →（识别到直播则）监控并在断流时用同一 URL 重开 →
        结束后等待 → 重新 resolve。开播后不再反复"识别是否直播"；
        断流只重开当前 URL，URL 过期/多次失败才重新跑浏览器解析。
        未来接入每周开播排期时，把"本次是否要探测"的决策收敛在本方法
        （非开播时段直接 sleep 即可），其余逻辑不用动。
        """
        settings = self.settings
        backoff = settings.offline_backoff_base_sec
        while True:
            went_live = False
            try:
                went_live = self.run_streamer(streamer)
            except Exception as exc:  # noqa: BLE001
                log.exception("streamer_monitor_crashed", extra={
                    "streamer": streamer.name, "error": str(exc)})
            if went_live:
                # 刚下播/断流结束：短等待后快速重查，方便主播马上又开播。
                delay = settings.stream_end_retry_sec
                backoff = settings.offline_backoff_base_sec
            else:
                # 未开播/解析失败：指数退避，避免离线主播反复占串行解析锁。
                delay = backoff
                backoff = min(backoff * 2, settings.offline_backoff_max_sec)
            log.info("streamer_next_probe", extra={
                "streamer": streamer.name, "delay_sec": delay, "went_live": went_live})
            time.sleep(delay)

    def run_streamer(self, streamer) -> bool:
        """一次 resolve + 直播监控周期；返回本周期是否进入过 live。

        直播中 read 失败不再整段结束：URL 有效期内用同一 URL 直接重开拉流
        （不重跑浏览器解析），超过 reopen_max 次连续失败或 URL 过期才结束
        本周期，由 run_forever 重新 resolve。live_sessions / recognizer /
        观测累计在重开之间保持不变，避免会话每分钟抖动。
        """
        self.db.set_streamer_status(streamer.id, "starting", at=now())
        source = getattr(streamer, "capture_source", "stream")
        if source != "stream":
            return self._run_non_stream(streamer, source)

        try:
            result = self.resolver.resolve(streamer.platform, streamer.url)
        except Exception as exc:  # noqa: BLE001
            log.error("stream_resolve_exception", extra={
                "streamer": streamer.name, "error": str(exc)})
            self.db.set_streamer_status(streamer.id, "reconnecting", str(exc), now())
            return False
        if not result.ok:
            status = "offline" if result.offline else "reconnecting"
            self.db.set_streamer_status(
                streamer.id, status, result.error or "stream unavailable", now())
            return False

        settings = self.settings
        ocr = FrameOCR(settings.roi)
        session_id = None
        recognizer = None
        missing = 0
        next_code_poll = 0.0
        deadline = time.monotonic() + settings.url_ttl_sec
        reopen = 0
        try:
            while True:
                capture = None
                try:
                    capture = open_capture("stream", result.url)
                except Exception as exc:  # noqa: BLE001
                    log.warning("capture_open_failed", extra={
                        "streamer": streamer.name, "error": str(exc)})
                opened = capture is not None and capture.is_opened()
                if opened:
                    if session_id is None:
                        self.db.set_streamer_status(streamer.id, "live", at=now())
                        session_id = self.db.execute(
                            "INSERT INTO live_sessions(streamer_id,started_at,stream_url) VALUES(?,?,?)",
                            (streamer.id, now(), result.url)).lastrowid
                        recognizer = MatchCodeRecognizer(settings.pattern,
                                                         settings.confirmation_frames)
                    missing = 0
                    next_code_poll = 0.0
                    got_frame = False
                    while True:
                        ok, frame = capture.read()
                        if not ok:
                            break
                        got_frame = True
                        self.db.touch_streamer(streamer.id, now())
                        current_time = time.monotonic()
                        if current_time >= next_code_poll:
                            confirmed = recognizer.observe(ocr.read(frame))
                            if confirmed:
                                missing = 0
                                next_code_poll = current_time + settings.code_poll_interval_sec
                                self.collisions.confirmed(
                                    session_id, confirmed, now(),
                                    settings.confirmation_frames)
                            else:
                                next_code_poll = current_time + settings.sample_interval_sec
                                missing += 1
                                if missing == settings.missing_alert_frames:
                                    log.warning("match_code_not_visible", extra={
                                        "session_id": session_id,
                                        "streamer": streamer.name})
                        time.sleep(settings.sample_interval_sec)
                    capture.release()
                    if got_frame:
                        reopen = 0  # 健康读了一段，重新计数
                    else:
                        reopen += 1
                        log.warning("stream_read_ended_instantly", extra={
                            "streamer": streamer.name, "session_id": session_id})
                else:
                    if capture is not None:
                        capture.release()
                    reopen += 1
                    log.warning("stream_reopen_failed", extra={
                        "streamer": streamer.name, "reopen": reopen,
                        "deadline_reached": time.monotonic() >= deadline})
                if reopen > settings.reopen_max or time.monotonic() >= deadline:
                    break
                time.sleep(settings.reopen_interval_sec)
            if session_id is None:
                self.db.set_streamer_status(streamer.id, "reconnecting",
                                            "stream url unusable", now())
                return False
            return True
        finally:
            if session_id is not None:
                self.collisions.end_code(session_id)
                self.db.execute("UPDATE live_sessions SET ended_at=? WHERE id=?",
                                (now(), session_id))
                self.db.set_streamer_status(streamer.id, "reconnecting",
                                            "stream ended; retrying", now())

    def _run_non_stream(self, streamer, source) -> bool:
        """camera / file 等固定输入源：打开后一直读到结束。"""
        settings = self.settings
        capture_input = streamer.capture_input
        try:
            capture = open_capture(source, capture_input)
        except (ImportError, RuntimeError, TypeError, ValueError) as exc:
            self.db.set_streamer_status(streamer.id, "reconnecting", str(exc), now())
            log.error("capture_open_failed", extra={"streamer": streamer.name, "error": str(exc)})
            return False
        if not capture.is_opened():
            self.db.set_streamer_status(streamer.id, "reconnecting", "capture is not opened", now())
            log.error("stream_open_failed", extra={"streamer": streamer.name})
            return False
        self.db.set_streamer_status(streamer.id, "live", at=now())
        session_id = self.db.execute(
            "INSERT INTO live_sessions(streamer_id,started_at,stream_url) VALUES(?,?,?)",
            (streamer.id, now(), None)).lastrowid
        recognizer = MatchCodeRecognizer(settings.pattern, settings.confirmation_frames)
        ocr = FrameOCR(settings.roi)
        missing = 0
        next_code_poll = 0.0
        try:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                self.db.touch_streamer(streamer.id, now())
                current_time = time.monotonic()
                if current_time >= next_code_poll:
                    confirmed = recognizer.observe(ocr.read(frame))
                    if confirmed:
                        missing = 0
                        next_code_poll = current_time + settings.code_poll_interval_sec
                        self.collisions.confirmed(session_id, confirmed, now(),
                                                  self.settings.confirmation_frames)
                    else:
                        next_code_poll = current_time + self.settings.sample_interval_sec
                        missing += 1
                        if missing == self.settings.missing_alert_frames:
                            log.warning("match_code_not_visible", extra={"session_id": session_id})
                time.sleep(self.settings.sample_interval_sec)
        finally:
            capture.release()
            self.collisions.end_code(session_id)
            self.db.execute("UPDATE live_sessions SET ended_at=? WHERE id=?",
                            (now(), session_id))
            self.db.set_streamer_status(streamer.id, "reconnecting", "stream ended; retrying", now())
        return True

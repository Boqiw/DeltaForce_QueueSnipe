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

    def run_streamer(self, streamer):
        self.db.set_streamer_status(streamer.id, "starting", at=now())
        source = getattr(streamer, "capture_source", "stream")
        if source == "stream":
            url = self.resolver.resolve(streamer.platform, streamer.url)
            if not url:
                self.db.set_streamer_status(
                    streamer.id, "reconnecting",
                    getattr(self.resolver, "last_error", None) or "stream unavailable", now())
                return False
            capture_input = url
        else:
            url = None
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
            (streamer.id, now(), url)).lastrowid
        recognizer = MatchCodeRecognizer(self.settings.pattern, self.settings.confirmation_frames)
        ocr = FrameOCR(self.settings.roi)
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
                        next_code_poll = current_time + self.settings.code_poll_interval_sec
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
            self.db.execute("UPDATE live_sessions SET ended_at=? WHERE id=?", (now(), session_id))
            self.db.set_streamer_status(streamer.id, "reconnecting", "stream ended; retrying", now())
        return True

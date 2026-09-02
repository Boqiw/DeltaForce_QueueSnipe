import logging, shlex, subprocess, sys
import threading
from urllib.parse import urlparse

log = logging.getLogger(__name__)

class StreamResolver:
    def resolve(self, platform, room_url): raise NotImplementedError

class StreamlinkResolver(StreamResolver):
    # 抖音对同一 IP 的并发浏览器访问会触发验证码，必须串行化解析。
    _douyin_lock = threading.Lock()

    def __init__(self, executable="streamlink", quality="best", platform_commands=None):
        self.executable, self.quality = executable, quality
        self.platform_commands = platform_commands or {}
        self.last_error = None

    @staticmethod
    def _candidate_urls(platform, room_url):
        if platform != "douyin":
            return [room_url]
        parsed = urlparse(room_url)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[-2] == "live":
            room_id = parts[-1]
            candidates = [room_url, f"https://live.douyin.com/{room_id}"]
            return list(dict.fromkeys(candidates))  # 去重，避免重复拉起浏览器
        return [room_url]

    def resolve(self, platform, room_url):
        command = self.platform_commands.get(platform)
        last_error = None
        if platform == "douyin":
            # 串行化：同一时刻只允许一个抖音浏览器实例，避免并发触发风控验证码。
            self._douyin_lock.acquire()
        try:
            for candidate in self._candidate_urls(platform, room_url):
                if command:
                    args = shlex.split(command.format(url=candidate))
                else:
                    args = [sys.executable, "-m", "streamlink", "--stream-url", candidate, self.quality]
                for attempt in range(3):
                    try:
                        result = subprocess.run(args, capture_output=True, text=True, timeout=90, check=True)
                        url = result.stdout.strip().splitlines()[-1]
                        if not url:
                            raise RuntimeError("resolver returned empty URL")
                        return url
                    except (OSError, subprocess.SubprocessError, IndexError, RuntimeError) as exc:
                        detail = getattr(exc, "stderr", None)
                        last_error = (detail or str(exc)).strip()
                        self.last_error = last_error
                        log.warning("stream_resolve_retry", extra={
                            "platform": platform, "candidate": candidate, "attempt": attempt + 1,
                            "error": last_error,
                        })
            log.error("stream_resolve_failed", extra={"platform": platform, "error": last_error or "unknown"})
            return None
        finally:
            if platform == "douyin":
                self._douyin_lock.release()

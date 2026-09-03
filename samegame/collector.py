import json, logging, shlex, subprocess, sys
import threading
from dataclasses import dataclass
from urllib.parse import urlparse

log = logging.getLogger(__name__)

@dataclass
class BoardRoom:
    """板块发现到的单个在播房间。viewer_count 未知时为 None。"""
    room_id: str
    name: str = ""
    viewer_count: int | None = None

@dataclass
class BoardResult:
    ok: bool
    rooms: list
    error: str | None = None

class StreamResolver:
    def resolve(self, platform, room_url): raise NotImplementedError

@dataclass
class ResolveResult:
    """一次解析的结构化结果。

    url     非空 = 拿到可播放流地址（直播中）
    offline True = 明确检测到未开播（正常状态，不应重试，也不应反复触发解析）
    error   机制性错误（超时/验证码/解析失败）文案
    """
    url: str | None = None
    offline: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.url)

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

    def resolve(self, platform, room_url) -> ResolveResult:
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
                        if "__OFFLINE__" in result.stdout:
                            # 明确未开播：正常状态，立即返回，不重试，避免离线主播
                            # 长时间占用抖音串行解析锁、阻塞在线主播。
                            log.info("stream_resolve_offline", extra={
                                "platform": platform, "candidate": candidate})
                            return ResolveResult(offline=True, error="主播未开播")
                        url = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
                        if not url:
                            raise RuntimeError("resolver returned empty URL")
                        return ResolveResult(url=url)
                    except (OSError, subprocess.SubprocessError, IndexError, RuntimeError) as exc:
                        detail = getattr(exc, "stderr", None)
                        last_error = (detail or str(exc)).strip()
                        self.last_error = last_error
                        log.warning("stream_resolve_retry", extra={
                            "platform": platform, "candidate": candidate, "attempt": attempt + 1,
                            "error": last_error,
                        })
            log.error("stream_resolve_failed", extra={"platform": platform, "error": last_error or "unknown"})
            return ResolveResult(error=last_error or "unknown")
        finally:
            if platform == "douyin":
                self._douyin_lock.release()


def fetch_board(board_url: str, scrolls: int = 8, timeout: float = 180.0) -> BoardResult:
    """抓取抖音板块在播房间列表。

    板块页同样要走有头浏览器，且必须与直播间解析共用同一个抖音浏览器
    profile —— 因此在 StreamlinkResolver._douyin_lock 内以子进程方式运行
    samegame.douyin_board，保证不与房间解析并发抢同一个 profile/触发风控。
    """
    if not board_url:
        return BoardResult(ok=False, rooms=[], error="board_url is empty")
    args = [sys.executable, "-m", "samegame.douyin_board", board_url, str(scrolls)]
    try:
        with StreamlinkResolver._douyin_lock:
            proc = subprocess.run(args, capture_output=True, text=True,
                                  timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return BoardResult(ok=False, rooms=[], error=f"board crawl timeout ({timeout:.0f}s)")
    except OSError as exc:
        return BoardResult(ok=False, rooms=[], error=str(exc))
    rooms: list[BoardRoom] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        rid = str(rec.get("room_id") or "")
        if not rid.isdigit():
            continue
        viewer = rec.get("viewer_count")
        rooms.append(BoardRoom(
            room_id=rid,
            name=str(rec.get("name") or "").strip(),
            viewer_count=int(viewer) if isinstance(viewer, (int, float))
            and not isinstance(viewer, bool) else None,
        ))
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip() or f"exit code {proc.returncode}"
        return BoardResult(ok=False, rooms=rooms, error=detail[:500])
    return BoardResult(ok=True, rooms=rooms)

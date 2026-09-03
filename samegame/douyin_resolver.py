"""抖音直播间取流 resolver（Playwright 有头 + 持久化浏览器配置方案）。

抖音对非浏览器请求会弹验证码中间页，streamlink 纯 HTTP 插件已失效。
本模块用有头 Chromium 打开直播间，拦截 `/webcast/room/web/enter/` 接口，
从中提取 FLV 流地址（FULL_HD1 优先，依次降级 HD1/SD1）。

关键：使用持久化浏览器上下文（user_data_dir=.douyin_profile）。
首次运行若弹验证码，需人工在打开的浏览器窗口里完成一次滑块验证，
通过后 cookie 会落盘；此后复用同一 profile 通常不再触发验证码。

用法（作为 collector 的 resolver_command）：
    <venv python> samegame/douyin_resolver.py {url}

输出约定（collector 只解析 stdout 一行）：
    成功    -> 打印一行可播放 FLV URL，退出码 0
    未开播  -> 打印一行 __OFFLINE__，退出码 0（明确检测到主播不在直播，
               不要把它当错误重试，否则离线主播会长时间占用串行解析锁）
    失败    -> 不打印 stdout，退出码非 0
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

OFFLINE = "__OFFLINE__"

_ANTIDETECT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
window.chrome = {runtime: {}};
"""

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 持久化浏览器 profile 目录：验证码通过后的 cookie 落盘于此，复用可免重复验证。
_PROFILE_DIR = Path(__file__).resolve().parent.parent / ".douyin_profile"


def _room_url(url: str) -> str:
    """规整为 https://live.douyin.com/<room_id> 形式。"""
    if url.isdigit():
        return f"https://live.douyin.com/{url}"
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 2 and parts[-2] == "live":
        return f"https://live.douyin.com/{parts[-1]}"
    return url


def _move_browser_on_screen(title: str) -> None:
    """把（屏幕外的）Chromium 窗口移回可见区域并置前，供用户手动过验证码。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    SW_RESTORE = 9
    SWP_NOSIZE = 0x0001
    SWP_NOZORDER = 0x0004
    SWP_SHOWWINDOW = 0x0040
    KEYWORDS = ("验证码", "安全验证", "滑动验证", "抖音")

    def _bring(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        # 移到屏幕可见区域（约 200,150），不改变窗口大小。
        user32.SetWindowPos(hwnd, 0, 200, 150, 0, 0,
                            SWP_NOSIZE | SWP_NOZORDER | SWP_SHOWWINDOW)
        try:
            user32.SetForegroundWindow(hwnd)
        except Exception:  # noqa: BLE001
            pass

    try:
        # 1) 优先按完整标题精确匹配（标题即页面标题）。
        hwnd = user32.FindWindowW(None, title or "")
        if hwnd:
            _bring(hwnd)
            return
        # 2) 兜底：枚举顶层窗口，按标题关键词匹配。由于 collector 对抖音解析做了
        #    串行化，同一时刻最多只有一个 Playwright 浏览器实例，误伤风险很低。
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _cb(hwnd_, _lparam):
            length = user32.GetWindowTextLengthW(hwnd_)
            if 0 < length < 512:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd_, buf, length + 1)
                text = buf.value
                if text and any(k in text for k in KEYWORDS) and text != title:
                    _bring(hwnd_)
                    return False  # 停止枚举
            return True

        user32.EnumWindows(WNDENUMPROC(_cb), 0)
    except Exception:  # noqa: BLE001
        pass  # 移动窗口失败不致命：用户仍可从任务栏手动恢复


def resolve(room_url: str, timeout_sec: float = 30.0,
            captcha_timeout_sec: float = 180.0) -> tuple[str | None, bool]:
    """打开直播间，拦截 enter 接口，返回 (flv_pull_url, offline)。

    offline=True 表示 enter 接口明确返回"主播未开播"（status != 2），
    调用方应将其视为正常状态而非错误，不要重试解析。
    """
    from playwright.sync_api import sync_playwright

    target = _room_url(room_url)
    found: dict = {"url": None, "error": None, "title": None, "offline": False}

    def on_response(resp):
        if "/webcast/room/web/enter/" not in resp.url:
            return
        if found["url"]:
            return
        try:
            data = resp.json()
            rooms = data.get("data", {}).get("data", [])
            if not rooms:
                found["error"] = "room not live or enter returned no data"
                return
            room = rooms[0]
            found["status"] = room.get("status")
            flv = (room.get("stream_url") or {}).get("flv_pull_url") or {}
            found["flv_keys"] = sorted(flv.keys())
            if room.get("status") not in (2, "2"):
                found["offline"] = True
                found["error"] = f"room not live (status={room.get('status')})"
                return
            for key in ("FULL_HD1", "HD1", "SD1"):
                u = flv.get(key)
                if u:
                    found["url"] = u
                    return
            found["error"] = "no flv_pull_url in enter response"
        except Exception as exc:  # noqa: BLE001
            found["error"] = f"parse enter error: {exc}"

    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(_PROFILE_DIR),
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-gpu",
                "--no-sandbox",
                # 必须保留有头模式（headless=True 会触发抖音验证码），但把窗口移到
                # 屏幕外并最小化静音，避免每次解析都弹出直播画面窗口打扰用户。
                "--window-position=-32000,-32000",
                "--start-minimized",
                "--mute-audio",
                "--window-size=800,600",
            ],
            user_agent=_UA,
            viewport={"width": 1280, "height": 720},
            locale="zh-CN",
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.add_init_script(_ANTIDETECT)
            page.on("response", on_response)
            try:
                page.goto(target, wait_until="domcontentloaded", timeout=15000)
            except Exception:  # noqa: BLE001
                pass  # 页面加载超时不影响 enter 接口拦截

            deadline = time.time() + timeout_sec
            captcha_deadline: float | None = None
            captcha_warned = False
            while not found["url"]:
                # enter 接口已明确返回（下播/无数据/解析错误），立即退出，不再空等超时，
                # 避免下播主播长时间占用 collector 的串行化锁，阻塞在线主播。
                if found["error"]:
                    break
                if page.is_closed():
                    found["error"] = found["error"] or "browser window closed"
                    break
                try:
                    title = page.title()
                except Exception:  # noqa: BLE001
                    title = ""
                is_captcha = ("验证码" in title) or ("安全验证" in title) or ("滑动" in title)
                if is_captcha:
                    # 弹了验证码：等待用户手动完成一次，期间不退出。
                    if not captcha_warned:
                        captcha_warned = True
                        captcha_deadline = time.time() + captcha_timeout_sec
                        print("[douyin_resolve] 检测到验证码，已把浏览器窗口移到屏幕中间，"
                              "请在弹出的窗口里手动完成一次验证", file=sys.stderr)
                    # 把窗口从屏幕外移回可见区域，否则用户无法操作验证码。
                    _move_browser_on_screen(title)
                    if time.time() >= captcha_deadline:
                        found["error"] = found["error"] or "captcha timeout"
                        break
                elif captcha_warned:
                    # 验证码页已离开（可能已通过）：继续等 enter 接口返回流地址。
                    if time.time() >= captcha_deadline:
                        found["error"] = found["error"] or "captcha timeout"
                        break
                elif time.time() >= deadline:
                    found["error"] = found["error"] or f"timeout (title={title!r})"
                    break
                page.wait_for_timeout(500)
            try:
                found["title"] = page.title()
            except Exception:  # noqa: BLE001
                pass
        finally:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass

    if not found["url"] and not found["offline"]:
        print(f"[douyin_resolve] failed: error={found['error']} title={found['title']} "
              f"status={found.get('status')} flv_keys={found.get('flv_keys')}",
              file=sys.stderr)
    return found["url"], bool(found.get("offline"))


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: douyin_resolver.py <room_url>", file=sys.stderr)
        return 2
    url, offline = resolve(argv[0])
    if url:
        print(url)
        return 0
    if offline:
        print(OFFLINE)
        return 0
    print("douyin resolve failed", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

"""三角洲板块在播房间抓取 CLI（Playwright，与 douyin_resolver 共用持久 profile）。

板块列表页由页面 JS 自己算签名，我们只"偷听"其列表 JSON 接口即可拿到房卡
（web_rid/昵称/人气），不需要自己生成 a_bogus；JSON 抓不到时用 DOM 兜底抓
<a href="https://live.douyin.com/<数字>"> 卡片文本里的昵称与"x.x万"人气。

用法（被 collector.fetch_board 以子进程方式调用）：
    <venv python> -m samegame.douyin_board <board_url> [scrolls]

输出约定：stdout 每行一个 NDJSON {"room_id","name","viewer_count"}（按 room_id 去重，
人气未知时 viewer_count 为 null）。退出码 0 = 本次抓取流程跑完（可能 0 行，
即板块无在播房/无匹配）；启动失败退出码非 0。诊断信息一律走 stderr。
"""
from __future__ import annotations

import json
import re
import sys
import time

from .douyin_resolver import _ANTIDETECT, _PROFILE_DIR, _UA, _move_browser_on_screen

# 房卡 JSON 中的字段候选（按优先级取值）。
_ID_KEYS = ("web_rid", "room_id")
_NICK_KEYS = ("nickname",)
_VIEWER_KEYS = ("user_count", "view_count", "watch_num", "online_user_count", "viewers")
# 只偷听疑似"列表/卡片"类接口，避免解析每一条资源请求。
_URL_HINTS = ("category", "feed", "room", "rank", "list", "board", "search", "live")
_ROOM_HREF_RE = re.compile(r"live\.douyin\.com/(\d+)")
_VIEWER_SPAN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(亿|万|千)?")
_UNIT_FACTOR = {"亿": 100_000_000, "万": 10_000, "千": 1_000}


def parse_viewer_text(text: str | None) -> int | None:
    """把 "1.2万" / "3,200" / "1.5亿" 之类文本解析成人气整数；解析不出返回 None。"""
    if not text:
        return None
    compact = re.sub(r"[\s,，]", "", str(text))
    m = _VIEWER_SPAN_RE.search(compact)
    if not m:
        return None
    try:
        value = float(m.group(1))
    except ValueError:
        return None
    factor = _UNIT_FACTOR.get(m.group(2) or "", 1)
    return int(value * factor)


def _scan_nick(node, depth=2) -> str | None:
    """在 node 子树（限 depth 层）里找昵称文本。"""
    if depth < 0 or node is None:
        return None
    if isinstance(node, dict):
        for key in _NICK_KEYS:
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in node.values():
            found = _scan_nick(value, depth - 1)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _scan_nick(value, depth - 1)
            if found:
                return found
    return None


def _scan_viewer(node, depth=1) -> int | None:
    """在 node 子树（限 depth 层）里找人气数值字段。"""
    if depth < 0 or node is None:
        return None
    if isinstance(node, dict):
        for key in _VIEWER_KEYS:
            value = node.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return int(value)
        for value in node.values():
            found = _scan_viewer(value, depth - 1)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _scan_viewer(value, depth - 1)
            if found is not None:
                return found
    return None


def extract_rooms_from_json(obj, max_rooms: int = 2000) -> dict:
    """递归扫描列表 JSON，收集房卡 {room_id -> {name, viewer_count}}。

    只认含 web_rid/room_id（纯数字）且子树里能找到昵称的对象，避免把主播
    user 对象、评论等无关数字误当成房间。viewer_count 取不到则为 None。
    """
    rooms: dict = {}

    def walk(node):
        if len(rooms) >= max_rooms:
            return
        if isinstance(node, dict):
            rid = None
            for key in _ID_KEYS:
                value = node.get(key)
                if value is not None and str(value).isdigit():
                    rid = str(value)
                    break
            if rid is not None:
                name = _scan_nick(node) or ""
                entry = rooms.setdefault(rid, {"name": name, "viewer_count": None})
                if not entry["name"]:
                    entry["name"] = name
                if entry["viewer_count"] is None:
                    viewer = _scan_viewer(node)
                    if viewer is not None:
                        entry["viewer_count"] = viewer
                return  # 房间对象内部不再深入，避免把房内 user 当新房间
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(obj)
    return rooms


def _viewer_from_lines(lines) -> int | None:
    """DOM 卡片 innerText 多行文本里挑人气：优先带单位行，其次小数值行。"""
    unit_hits, plain_hits = [], []
    for line in lines:
        compact = re.sub(r"[\s,，]", "", line)
        m = _VIEWER_SPAN_RE.search(compact)
        if not m:
            continue
        if m.group(2):
            unit_hits.append(parse_viewer_text(line))
        else:
            value = parse_viewer_text(line)
            if value is not None and value <= 100_000:
                plain_hits.append(value)
    if unit_hits:
        return max(h for h in unit_hits if h is not None)
    return max(plain_hits) if plain_hits else None


def _scrape_dom(page) -> dict:
    """抓当前可见的房卡 DOM：href 里的 room_id + 卡片文本昵称/人气。"""
    rooms: dict = {}
    anchors = page.query_selector_all('a[href*="live.douyin.com/"]')
    for anchor in anchors:
        try:
            href = anchor.get_attribute("href") or ""
        except Exception:  # noqa: BLE001
            continue
        m = _ROOM_HREF_RE.search(href)
        if not m:
            continue
        rid = m.group(1)
        entry = rooms.setdefault(rid, {"name": "", "viewer_count": None})
        lines = []
        try:
            text = anchor.inner_text() or ""
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        except Exception:  # noqa: BLE001
            pass
        if not entry["name"]:
            alt = aria = ""
            try:
                img = anchor.query_selector("img")
                alt = (img.get_attribute("alt") or "").strip() if img else ""
            except Exception:  # noqa: BLE001
                pass
            try:
                aria = (anchor.get_attribute("aria-label") or "").strip()
            except Exception:  # noqa: BLE001
                pass
            candidate = next(
                (ln for ln in (alt, aria, lines[0] if lines else "") if ln and len(ln) <= 40),
                "",
            )
            entry["name"] = (candidate or rid)[:40]
        if entry["viewer_count"] is None:
            viewer = _viewer_from_lines(lines)
            if viewer is not None:
                entry["viewer_count"] = viewer
    return rooms


def scrape_board(board_url: str, scrolls: int = 8, deadline: float = 150.0) -> dict:
    """打开板块页、滚动触发懒加载，返回 {room_id -> {name, viewer_count}}。

    JSON 偷听 + DOM 兜底双通道；JSON 优先级更高，DOM 只补 JSON 缺失的
    昵称/人气。本函数会真正拉起有头 Chromium，必须在 _douyin_lock 内调用。
    """
    from playwright.sync_api import sync_playwright

    rooms: dict = {}
    diagnostics = {"json_responses": 0, "dom_anchors": 0, "captcha": 0}

    def on_response(resp):
        try:
            ctype = resp.headers.get("content-type", "")
            if "json" not in ctype.lower():
                return
            length = int(resp.headers.get("content-length", "0") or 0)
            if length and length > 8_000_000:  # 太大跳过，防内存
                return
            url = resp.url.lower()
            if "douyin" not in url and "bytedance" not in url:
                return
            if not any(hint in url for hint in _URL_HINTS):
                return
            body = resp.text()
        except Exception:  # noqa: BLE001
            return
        try:
            data = json.loads(body)
        except Exception:  # noqa: BLE001
            return
        if not isinstance(data, (dict, list)):
            return
        diagnostics["json_responses"] += 1
        for rid, info in extract_rooms_from_json(data).items():
            entry = rooms.setdefault(rid, {"name": "", "viewer_count": None})
            if not entry["name"] and info["name"]:
                entry["name"] = info["name"]
            if entry["viewer_count"] is None and info["viewer_count"] is not None:
                entry["viewer_count"] = info["viewer_count"]

    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    start = time.time()
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(_PROFILE_DIR),
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-gpu",
                "--no-sandbox",
                "--window-position=-32000,-32000",
                "--start-minimized",
                "--mute-audio",
                "--window-size=1280,900",
            ],
            user_agent=_UA,
            viewport={"width": 1280, "height": 900},
            locale="zh-CN",
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.add_init_script(_ANTIDETECT)
            page.on("response", on_response)
            try:
                page.goto(board_url, wait_until="domcontentloaded", timeout=20000)
            except Exception:  # noqa: BLE001
                pass  # 页面加载超时不影响后续滚动与 JSON 偷听
            page.wait_for_timeout(1500)

            for index in range(scrolls):
                if time.time() - start > deadline:
                    break
                try:
                    title = page.title()
                except Exception:  # noqa: BLE001
                    title = ""
                if any(k in title for k in ("验证码", "安全验证", "滑动")):
                    diagnostics["captcha"] += 1
                    print("[douyin_board] 检测到验证码，已尝试把浏览器窗口移到屏幕中间，"
                          "请在弹出的窗口里手动完成一次验证", file=sys.stderr)
                    _move_browser_on_screen(title)
                    page.wait_for_timeout(2000)
                    continue
                try:
                    page.evaluate("window.scrollBy(0, window.innerHeight * 3)")
                    page.wait_for_timeout(1500)
                except Exception:  # noqa: BLE001
                    pass
                for rid, info in _scrape_dom(page).items():
                    entry = rooms.setdefault(rid, {"name": "", "viewer_count": None})
                    if not entry["name"] and info["name"]:
                        entry["name"] = info["name"]
                    if entry["viewer_count"] is None and info["viewer_count"] is not None:
                        entry["viewer_count"] = info["viewer_count"]
            try:
                diagnostics["dom_anchors"] = len(
                    page.query_selector_all('a[href*="live.douyin.com/"]'))
            except Exception:  # noqa: BLE001
                pass
        finally:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass

    print(f"[douyin_board] json_responses={diagnostics['json_responses']} "
          f"dom_anchors={diagnostics['dom_anchors']} captcha={diagnostics['captcha']} "
          f"rooms={len(rooms)}", file=sys.stderr)
    return rooms


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: douyin_board.py <board_url> [scrolls]", file=sys.stderr)
        return 2
    url = argv[0]
    try:
        scrolls = int(argv[1]) if len(argv) > 1 else 8
    except ValueError:
        scrolls = 8
    try:
        rooms = scrape_board(url, scrolls=scrolls)
    except Exception as exc:  # noqa: BLE001
        print(f"douyin_board failed: {exc}", file=sys.stderr)
        return 1
    for rid in sorted(rooms, key=lambda x: int(x)):
        info = rooms[rid]
        record = {
            "room_id": rid,
            "name": info["name"] or rid,
            "viewer_count": info["viewer_count"],
        }
        print(json.dumps(record, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

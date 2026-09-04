# 三角洲撞车监测（已归档 · 练手项目）
# Delta Force "Same-Match" Monitor — Archived / Learning Only

> ## ⚠️ 重要声明 / Important notice
> **本项目的核心假设已被实测推翻，已停止作为工具使用；仓库仅保留为技术练手存档。**
> **This project's core premise was disproven in practice. It is no longer used as a tool and is kept only as a learning archive.**

## 为什么这个方向不成立 / Why the premise was wrong

**一句话结论（TL;DR）**：抖音直播间画面左上角那行 `CN UID:<prefix>_<yyyymmddHHMM>` 的 `prefix` 是**游戏账号标识**——它与游戏账号绑定（表示"该游戏账号正在进行游戏"），对同一游戏账号长期固定，末尾 12 位只是墙钟分钟时间戳（每分钟、对所有直播间一起跳变）。它**不是**随对局变化的"对局代码"，因此"两个主播 prefix 相同 = 在同一局"从前提上就不成立，撞车判定不可能产生真实事件。

- **原设想（The original idea）**：OCR 每路直播左上角水印 → 把识别结果当作"对局码" → 两个不同主播出现同一"对局码"且首见时间接近 ⇒ 判定为同局（撞车）。
- **实测发现（What we found）**：`prefix` 与**游戏账号**绑定，表示"该游戏账号正在进行游戏"；对同一游戏账号它长期固定（主播换号会随之变化）。水印只标识正在直播的这一 个账号，不含同局其它玩家/主播的信息，因此不携带"这是哪一局"的信息。
- **结论（Conclusion）**：撞车判定失效，本项目停用。下方章节中"对局码识别 / 撞车判定"相关内容请视为**基于错误假设的实验代码**。

## 这个仓库还有什么价值 / What's still worth reading

业务目标虽不成立，代码里的**多路直播监控基础设施**仍可作为练手参考：

- **板块自动发现**：Playwright 有头浏览器拦截列表 JSON + 滚动懒加载，抓取抖音直播板块在播房卡（`douyin_board.py`）。
- **直播间取流解析**：拦截 `room/web/enter` 接口取 FLV URL，`FULL_HD1 → HD1 → SD1` 降级（`douyin_resolver.py`）。
- **风控处理经验**：抖音对并发浏览器会弹验证码 → 用持久化 profile 手动过一次验证码 + 全局**串行锁**串行化所有浏览器动作。
- **逐路常驻线程状态机**：解析 → 拉流 → OCR → 断流同 URL 重开 → 离线指数退避。
- OpenCV + RapidOCR 的 ROI 识别管线、SQLite 落库、Flask 本地看板、逐路线程隔离等工程实践。
- 已记录若干真实 bug 与教训（中文子进程管道编码、同主播多会话误判等）。

> 技术细节保留在下方章节，供存档与学习；其中"对局码识别 / 撞车判定"部分均为基于错误假设的实验逻辑。

## 原目标（存档） / Original goal (archived)

本地部署的多路直播监控系统：自动发现抖音「三角洲行动」板块里的高人气主播，对每一路在播画面做 ROI/OCR，识别左上角水印（当时误认为是对局代码），当两个不同主播在同一时间窗口"进入同一局"时判定为「撞车」事件，全部数据落 SQLite，通过 localhost Web 看板实时展示。**MVP 边界**：只做取流适配、水印识别、多路同码判定、落库与运营面板；录制、授权状态机、自动出片/发布留到 V1。

## 功能（已实现，按原假设存档）

- **板块自动发现**：定时抓取抖音板块页在播房间（`[discovery]`），全部登记入库；人气 ≥ `min_viewers` 的房间自动启用 OCR 监控；连续 `miss_limit` 轮不在板块即停用（历史行保留）。
- **高分主播监控**：每路启用主播一条常驻线程，自行完成「解析 → 直播监控 → 断流同 URL 重开 → 离线指数退避 → 重查」的循环。
- **水印前缀识别（原"对局码识别"，实验逻辑）**：按抖音 CNUID 水印的**稳定前缀**比对（剔除末尾 12 位分钟时间戳）。实测该前缀与游戏账号绑定（表示该账号正在游戏）而非对局码，此环节不产生真实的"对局"判定；保留仅作 ROI/OCR 管线示例。
- **同码判定（原"撞车判定"，实验逻辑）**：同一"前缀"在两个不同主播的活跃会话中出现且落在重叠窗口内 ⇒ 生成事件；同一直播间的崩溃残留/断流重开会话不会被误判。由于前缀非局代码，此类事件不表示真实同局。
- **Web 看板**：表格化展示主播状态/人气/当前水印前缀（面板标签仍写"局代码"，见上），只看直播中的主播，每 5 秒自动刷新；提供 JSON API。
- **逐路隔离**：每路主播独立的拉流器与 OCR 状态，单个主播解析失败/断流不影响其它主播。

## 数据流（原设计，含已失效环节）

```mermaid
flowchart LR
    A[板块页抓取<br/>douyin_board] -->|在播房间 NDJSON| B[apply_round 入库<br/>source=board]
    B -->|viewer ≥ 阈值 启用| C[LiveMonitor 线程×N]
    D[抖音直播间解析<br/>douyin_resolver] -->|FLV URL| C
    C -->|帧| E[ROI + OCR<br/>CNUID 前缀]
    E -->|prefix| F[同码判定 collided<br/>前提已推翻]
    F --> G[(SQLite)]
    G --> H[Flask 看板<br/>127.0.0.1:5000]
```

> `E → F` 环节依赖"prefix = 局代码"的错误假设，实际不产生有效撞车事件；其余基础设施（发现/取流/监控/看板）仍是可运行的完整示例。

## 项目结构

```
samegame/
  __main__.py          CLI 入口：seed → 板块发现线程 + 监控 worker + Flask
  config.py            TOML 加载（[app]/[ocr]/[discovery]/[[streamers]]/[platforms.*]）
  db.py                SQLite schema、自动迁移、seed（只清理/停用 manual 行）
  collector.py         StreamResolver 抽象、StreamlinkResolver 子进程解析、fetch_board
  douyin_resolver.py   抖音取流 CLI：Playwright 有头拦截 enter 接口取 FLV
  douyin_board.py      抖音板块 CLI：Playwright 偷听列表 JSON，DOM 兜底
  discovery.py         apply_round 纯函数 + DiscoveryService 定时抓板块
  monitor.py           LiveMonitor：每路主播常驻线程状态机
  capture.py           OpenCV 拉流（stream/camera/file），调高 FFmpeg 读取重试上限
  ocr.py               FrameOCR(ROI) + MatchCodeRecognizer（CNUID 前缀比对，原假设）
  collision.py         同码窗口期判定（实验逻辑；已确认前提错误）
  web.py               Flask 应用与 API
  templates/index.html 运营看板
tests/
  test_core.py         核心回归（含撞车/孤儿会话用例）
  test_discovery.py    板块 round 同步逻辑
```

## 运行

1. 安装 Python 3.12+。
2. `py -3.12 -m venv .venv; .venv\Scripts\Activate.ps1`
3. `pip install -e ".[dev,vision]"`（仅跑模拟/面板可 `pip install -e .`）
4. 复制 `config.example.toml` 为 `config.toml` 并按需填写。
5. `python -m samegame --config config.toml`
6. 浏览器打开 http://127.0.0.1:5000 （默认仅绑定 localhost）。

> 跑抖音取流/板块抓取还需 `pip install playwright` + `playwright install chromium`（与 `douyin_resolver.py` / `douyin_board.py` 同进程解释器）。

## 配置（config.toml）

```toml
[app]
database = "samegame.sqlite3"
host = "127.0.0.1"
port = 5000
sample_interval_sec = 2      # 拉帧采样间隔
code_poll_interval_sec = 45  # 同码续期检查间隔
confirmation_frames = 1      # 1 帧有效解析即确认；面板有噪声行可调 2~3
overlap_window_sec = 300     # 同码判定的首见时间窗口（实验逻辑）
url_ttl_sec = 900            # 拉流 URL 有效期（抖音 FLV 约 15 分钟）
reopen_max = 5               # URL 未过期时最多同 URL 重开次数
offline_backoff_base_sec = 30   # 未开播重查退避 base
offline_backoff_max_sec = 300   # 退避上限
stream_end_retry_sec = 15       # 下播/断流后短等待重查
log_level = "INFO"           # 日志为单行 JSON 结构化输出

[ocr]
# ROI 支持绝对像素 [x,y,w,h] 或 [0,1] 相对比例（自动适配分辨率）。
# 抖音 CN UID 水印在画面左上角最顶部，1080p 实测相对 x≈1%~19%、y≈0.6%~2.4%。
# 注意：prefix 与游戏账号绑定（表示该账号正在进行游戏），并非对局代码（详见顶部声明）。
roi = [0.005, 0.004, 0.20, 0.024]
# pattern 仅用于非 CNUID 通用短码的整串匹配。
pattern = "^CNUID[0-9]{33}$"
missing_alert_frames = 30

[discovery]
board_url = "https://live.douyin.com/categorynew/4_103_1_1_1_1011032"  # 留空 = 关闭板块发现
min_viewers = 10000    # 人气 >= 此值才启用 OCR 监控
interval_sec = 180     # 两轮抓取间隔
scrolls = 8            # 板块页滚动次数（触发懒加载）
miss_limit = 2         # 连续 N 轮不在板块 => 停用监控（保留历史）

[[streamers]]          # 手工监控的主播（与板块自动发现的并存）
platform = "douyin"
room_id = "401970645456"
name = "某主播"
monitor_enabled = true
priority = 5
url = "https://live.douyin.com/401970645456"
capture_source = "stream"   # stream / camera / file
capture_input = ""

[platforms.douyin]
# 解析命令，stdout 输出一行可播放 URL；{url} 替换为直播间 URL。
# 抖音用仓库自带解析器（见下节）；留空则回退 streamlink 插件。
resolver_command = ""
```

`[[streamers]]` 支持 `camera`（填 OBS 虚拟摄像头编号）与 `file`（本地视频）用于离线测试：

```toml
# capture_source = "camera"
# capture_input = 0
# capture_source = "file"
# capture_input = "test_data/live.mp4"
```

## 抖音接入

抖音对非浏览器请求会弹验证码中间页，streamlink 纯 HTTP 插件已失效。仓库提供两个 Playwright 有头浏览器工具（与人工过验证码的持久化 profile 配套）：

```powershell
# 直播间取流：拦截 /webcast/room/web/enter/ 接口，取 FLV（FULL_HD1→HD1→SD1 降级）
.\.venv\Scripts\python.exe samegame/douyin_resolver.py {url}

# 板块在播房间：偷听列表 JSON 接口（web_rid/昵称/人气），抓不到再用 DOM 兜底
.\.venv\Scripts\python.exe -m samegame.douyin_board <板块URL> [scrolls]
```

- 两者共用持久化浏览器 profile（仓库根目录 `.douyin_profile`）。**首次运行若弹验证码，浏览器窗口会自动移到屏幕中央，请手动完成一次滑块验证**；cookie 落盘后复用同一 profile 通常不再触发。
- 有头模式窗口默认移到屏幕外并最小化静音，避免每次解析弹出直播画面。
- 抖音对同一 IP 的并发浏览器会触发风控：解析与板块抓取都受同一把**串行锁**（`StreamlinkResolver._douyin_lock`）保护，任意时刻只有一个抖音浏览器实例。
- 子进程输出约定：成功 = 一行可播放 URL、退出码 0；明确未开播 = 一行 `__OFFLINE__`、退出码 0（视为正常，不重试）；失败 = 无 stdout、非 0 退出码。

没有可用解析器时记录结构化错误并继续监控其它主播；平台解析器可整体替换而不改业务代码。项目不包含爬虫、签名算法或非官方战绩 API。

## 同码判定口径（实验逻辑 · 前提已被推翻）

> ⚠️ 以下口径依赖"CNUID prefix = 对局码"这一**已被推翻**的假设：prefix 实为与**游戏账号**绑定的标识（表示该账号正在进行游戏），只标识直播中这一个账号，不含同局其它玩家信息。保留本段仅为记录当时的比对设计。

1. 每路活跃会话持续 OCR；识别键 = CNUID 数字串**去掉末尾 12 位分钟时间戳的稳定前段**。`CN UID:<prefix>_<yyyymmddHHMM>` 中的时间戳每分钟跳变、且同一分钟对所有直播间都相同，没有判别力，故不进入比对/存储键。
2. 两个**不同主播**的活跃会话出现相同"前缀"，且首见时间差 ≤ `overlap_window_sec` ⇒ 生成 `collision_events`（open）。因前缀绑定的是游戏账号而非局代码，此类事件不代表真实同局。
3. 同一主播的多个会话（崩溃残留/断流重开/重启孤儿）永不互判（此过滤仍有效）。

## 看板与 API

页面 http://127.0.0.1:5000/：

![本地看板运行截图](docs/panel.png)

> 上图为本机运行示例：板块自动发现 + 高分主播自动启用监控，28 路直播/连接中实时展示（截图时可复现），并随状态自动刷新。

- **监控主播**：三列表格（主播名字 / 观看人数 / 当前局代码）。面板标签沿用旧称"局代码"，实际展示的是 CNUID 水印前缀（游戏账号标识，见顶部声明）。默认只显示直播中（`online`）的主播并按人气降序；状态徽标含 直播中 / 正在连接 / 重连中 / 等待开播 / 未直播。
- **撞车事件**：最近 100 条（事件基于上述已被推翻的假设，不表示真实同局）。
- **素材清单**：整局录制已按决策后移至 V1，此区块固定提示无素材。

| 接口 | 说明 |
| --- | --- |
| `GET /api/streamers` | 主播全量行（含 `online`、`current_match_code`（实为水印前缀）、人气等） |
| `GET /api/collisions` | 事件最近 100 条（含参与主播名聚合） |
| `GET /api/materials` | V1 预留，固定返回 `deferred_to_v1` |
| `POST /api/observations` | 外部注入识别码（测试/模拟器用）：`{platform, room_id, name, code, ...}` |

监控线程与 Flask 刻意隔离——任何路由异常都不会停掉监控与发现线程。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

核心逻辑（识别归一化、同码判定、板块 round 同步）为纯函数，未安装 OpenCV/RapidOCR 也能跑测试与面板；生产识别建议安装 `.[vision]` 并按实际画面标定 ROI。测试中的"撞车/孤儿会话"用例针对的是上述实验判定逻辑。

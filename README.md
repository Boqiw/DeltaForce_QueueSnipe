# 三角洲行动主播撞车监测（MVP）

本项目只做：`streamlink` 拉流适配接口、画面 ROI/OCR 对局代码识别、多路撞车判定、SQLite 落库和 localhost 运营面板。**录制、授权状态机、自动出片/发布均留到 V1，MVP 不会启动录制或发布。**

## 运行

1. 安装 Python 3.12+，可选安装 `streamlink` 并确保其在 PATH。
2. `py -3.12 -m venv .venv; .venv\Scripts\Activate.ps1`
3. `pip install -e ".[dev,vision]"`（仅跑模拟/面板可 `pip install -e .`）
4. 复制 `config.example.toml` 为 `config.toml`，填写主播和直播间 URL。
5. `python -m samegame --config config.toml`
6. 浏览器打开 http://127.0.0.1:5000 （默认仅绑定 localhost）。

抖音等平台必须通过配置指定可替换的解析命令/插件；项目不包含爬虫、签名算法或非官方战绩 API。没有可用解析器时会记录结构化错误并继续监控其它主播。

每个主播可以单独选择画面来源：`stream` 使用平台解析器获取的直播流，`camera` 使用 OpenCV 摄像头（可填 OBS 虚拟摄像头编号），`file` 使用本地视频文件。示例：

```toml
[[streamers]]
platform = "douyin"
room_id = "645268872452"
name = "测试主播"
url = "https://live.douyin.com/645268872452"
capture_source = "stream"

# OBS 虚拟摄像头或本地视频测试时，将上面改为：
# capture_source = "camera"
# capture_input = 0
# 或：
# capture_source = "file"
# capture_input = "test_data/live.mp4"
```

每个主播的监控任务拥有独立的视频读取器和 OCR 状态；一个主播解析或断流失败不会停止其它主播。

## 测试

`pytest -q`

识别层没有安装 OpenCV/RapidOCR 时仍可运行核心逻辑和面板；生产识别建议安装 `.[vision]` 并按实际画面标定 ROI。

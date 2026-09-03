import re
from datetime import datetime

# 抖音防盗水印/对局码行结构（已实测 2026-09-03）：
#   CN UID:<prefix 20~21位>_<yyyymmddHHMM 12位>
# 末尾 12 位是墙钟分钟时间戳，每分钟跳变，同一分钟对任何直播间都相同，
# 因此没有对局判别力，不能进入比对/存储键。prefix 才是同局共有标识。
# 识别只保留 prefix，时间维度由观测表 first_seen_at/last_seen_at 记录。
_TS_LEN = 12
_CN_UID_RE = re.compile(r"CNUID(\d+)")


class MatchCodeRecognizer:
    """对局码识别。

    抖音 CNUID 行按「去掉末尾 12 位分钟时间戳的稳定前段」识别（跨分钟翻转键不变）。
    pattern 仅用于非 CNUID 的通用短码（如单测 / 未来平台），走整串匹配。
    """

    def __init__(self, pattern=r"^CNUID[0-9]{33}$", confirmation_frames=3):
        self.regex = re.compile(pattern)
        self.required = max(1, confirmation_frames)
        self._recent = []

    # ---- 文本清洗 ----
    @staticmethod
    def _clean(text):
        value = re.sub(r"[^A-Za-z0-9]", "", text or "").upper()
        # 修正常见 OCR 前缀误读（0/O/D/1/I 混淆）为 CNUID。
        return re.sub(r"^CN(?:0ID|01D|01UA|1D|U1D)", "CNUID", value)

    @staticmethod
    def _split_timestamp(digits: str) -> str | None:
        """在 CNUID 后的数字串里定位末尾 12 位 yyyymmddHHMM 时间戳的边界，
        返回其前面的 prefix（同局标识）；定位不可靠时返回 None。

        扫描从右往左：真时间戳紧贴 prefix，OCR 噪声只会追加在其后，
        因此从最右的合法时间窗开始找，可顺带丢掉尾部噪声；prefix 长度限制
        8~40 位防止误把整条噪声当 prefix。
        """
        n = len(digits)
        if n < _TS_LEN + 8:
            return None
        for j in range(n - _TS_LEN, -1, -1):
            prefix = digits[:j]
            if len(prefix) > 40:  # 太靠右说明整段噪声，继续左移找真时间戳
                continue
            if len(prefix) < 8:
                break
            seg = digits[j:j + _TS_LEN]
            try:
                ts = datetime.strptime(seg, "%Y%m%d%H%M")
            except ValueError:
                continue
            if not 2000 <= ts.year <= 2099:
                continue
            return prefix  # 从右往左首个合法时间窗 => 最可信的 prefix
        return None

    def normalize(self, text):
        """返回稳定识别键：
        - CNUID 行 -> 'CNUID<prefix>'（不含时间戳，跨分钟稳定）
        - 其它文本 -> 清洗后整串（须 fullmatch pattern）
        无法可靠识别 -> None。
        """
        value = self._clean(text)
        if not value.startswith("CNUID"):
            return value if self.regex.fullmatch(value) else None
        m = _CN_UID_RE.match(value)
        if not m:
            return None
        prefix = self._split_timestamp(m.group(1))
        if prefix is None:
            return None
        return "CNUID" + prefix

    def observe(self, text):
        code = self.normalize(text)
        if not code:
            self._recent.clear()
            return None
        self._recent.append(code)
        self._recent = self._recent[-self.required:]
        if len(self._recent) == self.required and len(set(self._recent)) == 1:
            return code
        return None

class FrameOCR:
    """Optional OpenCV/RapidOCR adapter; dependency-free core remains testable."""
    def __init__(self, roi):
        self.roi = roi
        try:
            from rapidocr_onnxruntime import RapidOCR
            self.engine = RapidOCR()
        except ImportError:
            self.engine = None
    def read(self, frame):
        if self.engine is None:
            return None
        x, y, w, h = self.roi
        # A ROI whose values all lie in [0, 1] is treated as a ratio of the
        # frame size (resolution-independent); otherwise it is absolute pixels.
        if all(0 <= float(v) <= 1 for v in (x, y, w, h)):
            fh, fw = frame.shape[:2]
            x, y = int(float(x) * fw), int(float(y) * fh)
            w, h = int(float(w) * fw), int(float(h) * fh)
        crop = frame[y:y + h, x:x + w]
        result, _ = self.engine(crop)
        return " ".join(item[1] for item in result) if result else None

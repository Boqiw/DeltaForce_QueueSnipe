import re
from collections import Counter

class MatchCodeRecognizer:
    def __init__(self, pattern=r"^CNUID[0-9]{33}$", confirmation_frames=3):
        self.regex, self.required = re.compile(pattern), confirmation_frames
        self._recent = []
    def normalize(self, text):
        value = re.sub(r"[^A-Za-z0-9]", "", text or "").upper()
        value = re.sub(r"^CN(?:0ID|01D|01UA|1D|U1D)", "CNUID", value)
        candidate = re.match(r"CNUID[0-9]{33}", value)
        if candidate and self.regex.fullmatch(candidate.group(0)):
            return candidate.group(0)
        return value if self.regex.fullmatch(value) else None
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

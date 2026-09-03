from __future__ import annotations

import os
from typing import Any

# OpenCV FFmpeg 后端在拉流没有新包时的重试上限，调高以缓解偶发
# "grabFrame packet read max attempts exceeded" 提前判死的问题。
# 必须在 import cv2 之前设置（cv2 只在构造 FrameCapture 时导入）。
os.environ.setdefault("OPENCV_FFMPEG_READ_ATTEMPTS", "4096")


class FrameCapture:
    def __init__(self, source: Any, camera: bool = False):
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("OpenCV is required for frame capture") from exc

        backend = cv2.CAP_DSHOW if camera else cv2.CAP_FFMPEG
        self._capture = cv2.VideoCapture(source, backend)

    def is_opened(self):
        return self._capture.isOpened()

    def read(self):
        return self._capture.read()

    def release(self):
        self._capture.release()


def open_capture(source: str, value: Any) -> FrameCapture:
    if source == "camera":
        return FrameCapture(int(value), camera=True)
    if source in {"stream", "file"}:
        return FrameCapture(value)
    raise ValueError(f"unsupported capture source: {source}")
from __future__ import annotations

from typing import Any


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
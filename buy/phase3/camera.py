"""USB 웹캠 캡처 — 폰 화면 이미지 가져오기. multi-frame voting으로 안정성 향상."""
from __future__ import annotations
import time
from typing import Optional

import cv2
import numpy as np


def capture_frame(cam_index: int = 0, warmup_ms: int = 200) -> Optional[np.ndarray]:
    """웹캠에서 1프레임 캡처. warmup으로 노출/포커스 안정화."""
    cap = cv2.VideoCapture(cam_index)
    if not cap.isOpened():
        return None
    # 최대 해상도 설정 (QHD 카메라면 1440p)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 2560)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1440)
    # warmup — auto-exposure가 안정화될 시간
    time.sleep(warmup_ms / 1000)
    # 버퍼 비우기 (오래된 frame 버림)
    for _ in range(3):
        cap.read()
    ret, frame = cap.read()
    cap.release()
    return frame if ret else None


def capture_n_frames(cam_index: int = 0, n: int = 3, interval_ms: int = 100) -> list[np.ndarray]:
    """N개 프레임 연속 캡처 (voting/노이즈 감소용)."""
    cap = cv2.VideoCapture(cam_index)
    if not cap.isOpened():
        return []
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 2560)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1440)
    time.sleep(0.2)
    for _ in range(3):
        cap.read()
    frames = []
    for _ in range(n):
        ret, frame = cap.read()
        if ret:
            frames.append(frame)
        time.sleep(interval_ms / 1000)
    cap.release()
    return frames

"""폰 정렬 검증 — 매번 캡처 시 reference 프레임과 비교해서 shift 감지.

캘리브레이션:
1. 폰을 정해진 위치에 정확히 놓음 (충전대 모서리에 폰 모서리 정렬)
2. reference 프레임 1장 캡처 → reference.png 저장
3. 동시에 keypad bbox / phone screen bbox 등 좌표 저장

매 결제 시:
1. 새 프레임 캡처
2. reference와 phase correlation으로 shift 계산
3. shift < 50px (약 5mm): 자동 보정해서 OCR 진행
4. shift 50-100px: 경고 + OCR 시도 (fail시 stop)
5. shift > 100px: 즉시 abort + 알림 ("폰 위치 이상")
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


CALIB_DIR = Path(__file__).resolve().parent / "calibration"
CALIB_DIR.mkdir(exist_ok=True)


def save_calibration(reference_frame: np.ndarray, keypad_bbox: tuple[int, int, int, int]) -> None:
    """캘리브레이션 1회: 정렬된 reference 프레임 + 키패드 bbox 저장."""
    cv2.imwrite(str(CALIB_DIR / "reference.png"), reference_frame)
    meta = {"keypad_bbox": keypad_bbox, "frame_shape": list(reference_frame.shape)}
    (CALIB_DIR / "meta.json").write_text(json.dumps(meta, indent=2))


def load_calibration() -> Optional[tuple[np.ndarray, dict]]:
    ref_path = CALIB_DIR / "reference.png"
    meta_path = CALIB_DIR / "meta.json"
    if not ref_path.exists() or not meta_path.exists():
        return None
    ref = cv2.imread(str(ref_path))
    meta = json.loads(meta_path.read_text())
    return ref, meta


def detect_shift(current: np.ndarray, reference: np.ndarray) -> tuple[int, int, float]:
    """phase correlation으로 (dx, dy, response_strength) 계산.
    response_strength: 0-1, 높을수록 매칭 신뢰도.
    """
    cur_gray = cv2.cvtColor(current, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255
    ref_gray = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255
    if cur_gray.shape != ref_gray.shape:
        # resize current to reference size
        cur_gray = cv2.resize(cur_gray, (ref_gray.shape[1], ref_gray.shape[0]))
    (dx, dy), response = cv2.phaseCorrelate(ref_gray, cur_gray)
    return int(round(dx)), int(round(dy)), float(response)


def check_alignment(current: np.ndarray, max_shift_px: int = 50) -> tuple[bool, dict]:
    """현재 프레임의 정렬 상태 확인.

    Returns:
        (ok, info)
        info = {dx, dy, shift_magnitude, response, action}
        action: "ok" | "warn" | "abort"
    """
    calib = load_calibration()
    if calib is None:
        return False, {"action": "abort", "reason": "no calibration"}
    reference, meta = calib

    dx, dy, response = detect_shift(current, reference)
    magnitude = int(np.hypot(dx, dy))

    info = {"dx": dx, "dy": dy, "shift_magnitude": magnitude, "response": response}
    if magnitude <= max_shift_px // 2:
        info["action"] = "ok"
        return True, info
    elif magnitude <= max_shift_px:
        info["action"] = "warn"  # warning but proceed
        return True, info
    elif magnitude <= max_shift_px * 2:
        info["action"] = "warn_proceed_with_caution"
        return True, info
    else:
        info["action"] = "abort"
        info["reason"] = f"shift {magnitude}px > {max_shift_px * 2}px threshold"
        return False, info


def apply_shift_correction(bbox: tuple[int, int, int, int], dx: int, dy: int) -> tuple[int, int, int, int]:
    """캡처에 shift가 감지되면 키패드 bbox도 그만큼 보정."""
    x1, y1, x2, y2 = bbox
    return (x1 + dx, y1 + dy, x2 + dx, y2 + dy)

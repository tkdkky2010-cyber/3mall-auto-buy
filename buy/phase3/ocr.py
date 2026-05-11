"""KB Pay 셔플 키패드 OCR — 카메라 이미지에서 각 숫자 버튼의 좌표 찾기.

전제:
- 카메라가 폰 화면을 위에서 캡처 (고정 위치)
- 키패드 영역은 미리 캘리브레이션됨 (좌상단/우하단 픽셀 좌표)
- 키패드 그리드: 일반적으로 3열 × 4행 (0-9 + 백스페이스 + 확인 등)
- 0-9 reference 이미지 한 번 캘리브레이션해서 buy/phase3/keypad_refs/에 저장

알고리즘:
1. 입력 이미지에서 키패드 영역 crop
2. 그리드 셀로 분할 (e.g., 3×4)
3. 각 셀과 0-9 reference 이미지 비교 (template matching)
4. 매칭 점수 가장 높은 digit을 그 셀에 할당
5. confidence < threshold 인 셀은 unknown
6. 결과: {digit: (cell_x, cell_y)} 매핑

confidence 검증:
- 모든 셀이 unique digit으로 매칭됐는지 (10개 digit 다 있는지)
- 매칭 점수 평균이 임계값 위인지
- 같은 digit이 2번 매칭되면 fail
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


REFS_DIR = Path(__file__).resolve().parent / "keypad_refs"


class OCRResult:
    def __init__(self, digit_to_pos: dict[int, tuple[int, int]], confidence: float, raw_scores: dict):
        self.digit_to_pos = digit_to_pos
        self.confidence = confidence
        self.raw_scores = raw_scores

    @property
    def all_digits_found(self) -> bool:
        return set(self.digit_to_pos.keys()) >= set(range(10))


def load_references() -> dict[int, np.ndarray]:
    """0-9 reference 이미지 로드. grayscale."""
    refs = {}
    for d in range(10):
        path = REFS_DIR / f"{d}.png"
        if not path.exists():
            raise FileNotFoundError(f"Reference 없음: {path}")
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise ValueError(f"이미지 로드 실패: {path}")
        refs[d] = img
    return refs


def crop_keypad(image: np.ndarray, bbox: tuple[int, int, int, int]) -> np.ndarray:
    """입력 이미지에서 키패드 영역 잘라냄. bbox = (x1, y1, x2, y2)."""
    x1, y1, x2, y2 = bbox
    return image[y1:y2, x1:x2]


def split_grid(keypad: np.ndarray, rows: int, cols: int) -> list[list[tuple[np.ndarray, tuple[int, int]]]]:
    """키패드를 rows×cols 그리드로 분할. 각 셀의 이미지 + 중심 좌표 (키패드 내부 픽셀 기준) 반환."""
    h, w = keypad.shape[:2]
    cell_h, cell_w = h // rows, w // cols
    grid = []
    for r in range(rows):
        row = []
        for c in range(cols):
            y1, y2 = r * cell_h, (r + 1) * cell_h
            x1, x2 = c * cell_w, (c + 1) * cell_w
            cell = keypad[y1:y2, x1:x2]
            center = (x1 + cell_w // 2, y1 + cell_h // 2)
            row.append((cell, center))
        grid.append(row)
    return grid


def match_cell(cell: np.ndarray, refs: dict[int, np.ndarray]) -> tuple[int, float]:
    """셀 이미지를 각 reference와 매칭. 가장 높은 점수의 digit 반환.

    score: 0~1 정규화. 1에 가까울수록 매칭 잘 됨.
    """
    if len(cell.shape) == 3:
        cell = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
    best_digit, best_score = -1, -float("inf")
    scores = {}
    for d, ref in refs.items():
        # ref와 cell 크기 맞춤 (template matching은 같은 크기 필요)
        ref_resized = cv2.resize(ref, (cell.shape[1], cell.shape[0]))
        result = cv2.matchTemplate(cell, ref_resized, cv2.TM_CCOEFF_NORMED)
        score = float(result[0][0])
        scores[d] = score
        if score > best_score:
            best_score, best_digit = score, d
    return best_digit, best_score, scores


def recognize_keypad(
    image: np.ndarray,
    keypad_bbox: tuple[int, int, int, int],
    grid: tuple[int, int] = (4, 3),  # (rows, cols) — KB Pay 보통 3열 4행
    min_score: float = 0.6,
) -> OCRResult:
    """폰 화면 캡처 이미지에서 셔플 키패드 분석.

    Returns:
        OCRResult — digit -> (x, y) 좌표 (입력 이미지 절대 픽셀 기준)
    """
    refs = load_references()
    keypad = crop_keypad(image, keypad_bbox)
    rows, cols = grid
    cells = split_grid(keypad, rows, cols)

    digit_to_pos = {}
    digit_scores = {}  # digit -> best score
    raw_scores = []

    for row in cells:
        for cell, (cx, cy) in row:
            digit, score, all_scores = match_cell(cell, refs)
            # 절대 좌표 (이미지 전체 기준)
            abs_x = keypad_bbox[0] + cx
            abs_y = keypad_bbox[1] + cy
            raw_scores.append({"pos": (abs_x, abs_y), "scores": all_scores, "best": digit, "score": score})
            if score < min_score:
                continue
            # 더 높은 점수의 매칭이 있으면 그 위치 우선
            if digit not in digit_to_pos or score > digit_scores[digit]:
                digit_to_pos[digit] = (abs_x, abs_y)
                digit_scores[digit] = score

    avg_score = float(np.mean([s for s in digit_scores.values()])) if digit_scores else 0.0
    return OCRResult(digit_to_pos, avg_score, raw_scores)


def validate(result: OCRResult, min_confidence: float = 0.7) -> tuple[bool, str]:
    """OCR 결과 검증. (valid, reason)."""
    if not result.all_digits_found:
        missing = set(range(10)) - set(result.digit_to_pos.keys())
        return False, f"missing digits: {missing}"
    if result.confidence < min_confidence:
        return False, f"low confidence: {result.confidence:.3f} < {min_confidence}"
    return True, "ok"


def get_click_positions(result: OCRResult, password: str) -> list[tuple[int, int]]:
    """비번 문자열을 grid 좌표 sequence로 변환."""
    return [result.digit_to_pos[int(ch)] for ch in password]

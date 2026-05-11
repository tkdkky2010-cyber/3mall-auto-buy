"""풀자동화 safety 가드. 카드 잠김 방지가 최우선.

원칙:
1. OCR confidence 낮으면 즉시 stop + 알림
2. 한 번이라도 비번 틀린 흔적 (KB Pay error 화면) 보이면 즉시 stop
3. 모든 결제 시도 logs + screenshot 저장 (사후 분석)
4. 카드 잠김 이력 발생 시 전체 운영 중단
"""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


LOGS_DIR = Path(__file__).resolve().parent.parent.parent / "logs" / "phase3"
LOGS_DIR.mkdir(parents=True, exist_ok=True)


def log_attempt(
    account_id: str,
    stage: str,
    image: Optional[np.ndarray],
    ocr_result: Optional[dict],
    success: bool,
    notes: str = "",
) -> str:
    """1회 결제 시도의 단계 로그 저장. 폴더는 timestamp+account 별 분리.

    Returns: log directory path
    """
    ts = time.strftime("%Y%m%d-%H%M%S")
    folder = LOGS_DIR / f"{ts}_{account_id}_{stage}"
    folder.mkdir(parents=True, exist_ok=True)

    if image is not None:
        cv2.imwrite(str(folder / "capture.png"), image)
    meta = {
        "timestamp": ts,
        "account_id": account_id,
        "stage": stage,
        "success": success,
        "notes": notes,
        "ocr_result": ocr_result,
    }
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return str(folder)


def check_confidence(result, min_confidence: float = 0.7, min_individual: float = 0.5) -> tuple[bool, str]:
    """OCR 결과 종합 confidence + 각 셀별 최저 신뢰도 검증.

    풀자동 운영 시 매우 보수적으로 잡아야 함:
    - min_confidence (평균): 0.7 이상
    - min_individual (최저): 0.5 이상 — 한 셀이라도 낮으면 abort
    """
    if not result.all_digits_found:
        missing = set(range(10)) - set(result.digit_to_pos.keys())
        return False, f"missing digits {missing}"
    if result.confidence < min_confidence:
        return False, f"avg confidence {result.confidence:.3f} < {min_confidence}"
    # 개별 confidence는 raw_scores에서 추출
    scores = [r["score"] for r in result.raw_scores if r["best"] in result.digit_to_pos]
    if scores and min(scores) < min_individual:
        return False, f"min individual confidence {min(scores):.3f} < {min_individual}"
    return True, "ok"


class CircuitBreaker:
    """반복 실패 발생 시 전체 운영 중단.

    상태 파일 (data/phase3_circuit.json) 로 영구 저장.
    카드 잠김 위험을 막기 위해 한 번이라도 실패하면 자동 차단.
    """
    STATE_FILE = LOGS_DIR.parent.parent / "data" / "phase3_circuit.json"

    def __init__(self):
        self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.state = self._load()

    def _load(self) -> dict:
        if self.STATE_FILE.exists():
            return json.loads(self.STATE_FILE.read_text())
        return {"tripped": False, "tripped_at": None, "reason": None, "fail_count": 0}

    def _save(self):
        self.STATE_FILE.write_text(json.dumps(self.state, ensure_ascii=False, indent=2))

    def is_open(self) -> bool:
        return self.state.get("tripped", False)

    def trip(self, reason: str):
        self.state.update({
            "tripped": True,
            "tripped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "reason": reason,
        })
        self._save()

    def record_fail(self) -> bool:
        """실패 1회 기록. 누적 N회 이상이면 trip.

        Returns: True if just tripped (newly tripped), False otherwise.
        """
        self.state["fail_count"] = self.state.get("fail_count", 0) + 1
        if self.state["fail_count"] >= 1:  # 매우 보수적: 1회라도 실패 = 차단
            self.trip(f"fail count reached {self.state['fail_count']}")
            self._save()
            return True
        self._save()
        return False

    def reset(self):
        """수동 reset (사용자가 문제 확인 + 카드 잠김 X 확인 후)."""
        self.state = {"tripped": False, "tripped_at": None, "reason": None, "fail_count": 0}
        self._save()

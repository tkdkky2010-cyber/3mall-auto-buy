"""고수준 API: PC가 호출. 폰에 비번 N자리 자동입력.

사용 예 (Lotte 자동화 코드에서):
    from buy.phase3 import phone_input

    # 7자리 OTP 입력
    phone_input.type_password(esp32_host, "1234567", keypad_type="numeric_fixed")

    # 6자리 비밀번호 입력 (셔플 키패드)
    phone_input.type_password(esp32_host, "137601", keypad_type="shuffle")
"""
from __future__ import annotations
import time
from typing import Optional

from . import alignment
from . import camera
from . import ocr
from . import safety
from .esp32_client import ESP32Client


class PhoneInputError(Exception):
    pass


def type_password(
    esp32_host: str,
    password: str,
    keypad_bbox: tuple[int, int, int, int],
    keypad_grid: tuple[int, int] = (4, 3),
    cam_index: int = 0,
    account_id: str = "unknown",
    safety_check: bool = True,
    min_confidence: float = 0.7,
    click_delay_ms: int = 200,
) -> bool:
    """폰 셔플 키패드에 비번 자동 입력.

    Args:
        esp32_host: ESP32 IP address
        password: 입력할 비번 (e.g., "137601")
        keypad_bbox: 기본 키패드 위치 (캘리브레이션 값)
        keypad_grid: 그리드 (rows, cols)
        cam_index: USB 웹캠 디바이스 인덱스 (보통 0)
        account_id: 로그용 식별자
        safety_check: True면 모든 안전장치 활성화 (운영)
        min_confidence: OCR confidence 최소값
        click_delay_ms: 클릭 간 대기

    Returns:
        True if successful, False if any safety check failed.

    Raises:
        PhoneInputError on critical errors (carries reason in message).
    """
    # circuit breaker — 이전 실패로 인한 운영 차단 확인
    cb = safety.CircuitBreaker()
    if cb.is_open():
        raise PhoneInputError(
            f"CircuitBreaker 차단됨 (tripped_at={cb.state.get('tripped_at')}, "
            f"reason={cb.state.get('reason')}). 수동 reset 필요."
        )

    # 1. 카메라 캡처
    frame = camera.capture_frame(cam_index)
    if frame is None:
        raise PhoneInputError("카메라 캡처 실패")

    # 2. 정렬 확인 (캘리브레이션 reference와 비교)
    aligned, info = alignment.check_alignment(frame)
    if not aligned:
        safety.log_attempt(account_id, "alignment_fail", frame, info, success=False)
        raise PhoneInputError(f"정렬 실패: {info.get('reason')}")
    # shift 보정
    dx, dy = info.get("dx", 0), info.get("dy", 0)
    corrected_bbox = alignment.apply_shift_correction(keypad_bbox, dx, dy)

    # 3. OCR로 셔플 키패드 분석
    ocr_result = ocr.recognize_keypad(frame, corrected_bbox, grid=keypad_grid, min_score=min_confidence * 0.7)

    # 4. confidence 검증
    if safety_check:
        ok, reason = safety.check_confidence(ocr_result, min_confidence=min_confidence)
        if not ok:
            safety.log_attempt(
                account_id,
                "ocr_low_confidence",
                frame,
                {"digit_to_pos": ocr_result.digit_to_pos, "confidence": ocr_result.confidence, "reason": reason},
                success=False,
            )
            raise PhoneInputError(f"OCR confidence 부족: {reason}")

    # 5. 클릭 좌표 생성
    try:
        positions = ocr.get_click_positions(ocr_result, password)
    except KeyError as e:
        raise PhoneInputError(f"비번 자릿수 매핑 실패: {e}")

    # 6. ESP32에 click sequence 전송
    client = ESP32Client(esp32_host)
    try:
        status = client.status()
        if status.get("wifi") != "ok":
            raise PhoneInputError(f"ESP32 WiFi 상태 비정상: {status}")
    except Exception as e:
        raise PhoneInputError(f"ESP32 연결 실패: {e}")

    success = client.click_sequence(positions, delay_ms=click_delay_ms)

    # 7. 로그
    safety.log_attempt(
        account_id,
        "click_sequence",
        frame,
        {"positions": positions, "password_length": len(password), "confidence": ocr_result.confidence},
        success=success,
        notes=f"alignment dx={dx} dy={dy} response={info.get('response'):.3f}",
    )

    return success


def type_otp(esp32_host: str, otp: str) -> bool:
    """USB HID Keyboard로 7자리 OTP 직접 입력 (일반 numeric keyboard 화면용).

    셔플 안 된 일반 numeric 키보드 화면에서 사용 (KB/현대/하나/농협/BC 7자리).
    더 빠르고 안정적. 단 KB Pay가 입력 받아주는지 검증 필요.
    """
    client = ESP32Client(esp32_host)
    return client.type_text(otp)

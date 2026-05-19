"""카드사별 PIN/코드 입력 — OCR + ESP32 통합.

사용 패턴:
    from phone_auto.pin_entry import enter_pin
    from phone_auto.esp32_client import ESP32Client

    esp = ESP32Client('172.30.1.17')
    success = enter_pin('hyundai_pin6', pin='123456', esp=esp)

각 카드 preset 의 layout 속성에 따라 동작 분기:
- "fixed_*"  → OCR 1회 캘리브레이션 (옵션 cache), 좌표 재사용
- "shuffled_*" → 매 호출마다 OCR 재실행 + 좌표 새로 잡음

검증 안전망:
- 다중 프레임 일관성 (3프레임 ±25px)
- 클릭 후 PIN dot 카운트 (옵션)
- ROI 검증 (preset 의 roi_y_frac)
"""
from __future__ import annotations
import time
from typing import Optional

from .ocr_keypad import (
    KEYPAD_PRESETS,
    vote_digits_for_card,
    vote_digits_multi_frame,
    pin_to_clicks,
    count_pin_dots,
    capture_frame,
)


# 고정 layout 캐시 (세션 중 1회 캘리브레이션 후 재사용)
_FIXED_DIGIT_CACHE: dict[str, dict[str, tuple[int, int]]] = {}


def calibrate_card(card_key: str, cam_idx: int = 0, force: bool = False) -> Optional[dict[str, tuple[int, int]]]:
    """고정 layout 카드의 좌표 1회 캘리브레이션 + 캐시.

    Args:
        card_key: KEYPAD_PRESETS 의 key (예: 'hyundai_code7')
        cam_idx: 카메라 인덱스
        force: True 면 캐시 무시 + 재캘리브레이션

    Returns digit_map or None.
    """
    if card_key not in KEYPAD_PRESETS:
        raise ValueError(f"unknown card: {card_key}")
    preset = KEYPAD_PRESETS[card_key]
    if not preset["layout"].startswith("fixed_"):
        raise ValueError(f"{card_key} is shuffled — calibration not applicable")
    if not force and card_key in _FIXED_DIGIT_CACHE:
        return _FIXED_DIGIT_CACHE[card_key]
    digit_map = vote_digits_for_card(card_key, cam_idx=cam_idx)
    if digit_map and len(digit_map) == 10:
        _FIXED_DIGIT_CACHE[card_key] = digit_map
        print(f"[calibrate] {card_key}: 캘리브레이션 완료 ({len(digit_map)}/10)")
        return digit_map
    print(f"[calibrate] {card_key}: 실패 ({len(digit_map) if digit_map else 0}/10)")
    return None


def get_digit_map(card_key: str, cam_idx: int = 0) -> Optional[dict[str, tuple[int, int]]]:
    """카드 preset 에 맞춰 digit_map 획득.

    - fixed_*: 캐시 있으면 재사용, 없으면 캘리브레이션
    - shuffled_*: 매번 새 OCR (다중 프레임 일관성)
    """
    if card_key not in KEYPAD_PRESETS:
        raise ValueError(f"unknown card: {card_key}")
    preset = KEYPAD_PRESETS[card_key]
    if preset["layout"].startswith("fixed_"):
        return calibrate_card(card_key, cam_idx)
    # shuffled: 매번 새 OCR + 다중 프레임 일관성
    return vote_digits_multi_frame(
        cam_idx=cam_idx,
        n_frames=3,
        pos_tolerance=25,
    )


def enter_pin(
    card_key: str,
    pin: str,
    esp,  # ESP32Client 인스턴스
    cam_idx: int = 0,
    inter_click_delay_sec: float = 0.4,
    verify_dots: bool = True,
    dot_verify_delay_sec: float = 0.3,
) -> bool:
    """카드 PIN/코드 입력 — OCR + ESP32 click + 각 자릿수 dot 검증.

    Args:
        card_key: KEYPAD_PRESETS 의 key (예: 'hyundai_pin6', 'hana_code7')
        pin: 입력할 PIN/코드 문자열 (digits only)
        esp: ESP32Client 인스턴스 (click 메서드 가짐)
        cam_idx: 카메라 인덱스 (Camo / Continuity / etc.)
        inter_click_delay_sec: 클릭 간 대기 (앱 반응)
        verify_dots: 클릭 후 PIN dot 개수 검증 여부
        dot_verify_delay_sec: 클릭 후 dot 검증 캡쳐까지 대기

    Returns True (모두 클릭 + 검증 성공) / False.
    """
    if card_key not in KEYPAD_PRESETS:
        raise ValueError(f"unknown card: {card_key}")
    if not pin or not pin.isdigit():
        raise ValueError(f"invalid PIN: {pin!r}")
    preset = KEYPAD_PRESETS[card_key]
    expected_len = preset.get("n_digits_pin")
    if expected_len and len(pin) != expected_len:
        raise ValueError(f"{card_key} expects {expected_len} digits, got {len(pin)}")

    print(f"[pin_entry] {card_key} — PIN {len(pin)}자리 입력 시작")
    print(f"           layout={preset['layout']}, flip_h={preset['flip_h']}, roi_y={preset['roi_y_frac']}")

    # 1) digit_map 획득
    digit_map = get_digit_map(card_key, cam_idx)
    if not digit_map:
        print(f"[pin_entry] {card_key}: digit_map 획득 실패 — abort")
        return False
    print(f"[pin_entry] digit_map: {len(digit_map)}/10 매핑")

    # 2) PIN → 클릭 좌표 시퀀스
    try:
        clicks = pin_to_clicks(pin, digit_map)
    except ValueError as e:
        print(f"[pin_entry] PIN 변환 실패: {e}")
        return False

    # 3) 각 자릿수 순차 클릭 + 검증
    for i, (x, y) in enumerate(clicks):
        print(f"  [{i+1}/{len(pin)}] click ({x}, {y}) — 자릿수 '{pin[i]}'")
        try:
            esp.click(x, y)
        except Exception as e:
            print(f"  ✗ ESP32 click 실패: {e}")
            return False
        time.sleep(inter_click_delay_sec)
        if verify_dots:
            time.sleep(dot_verify_delay_sec)
            tmp = capture_frame(cam_idx, warmup_frames=3)
            if not tmp:
                print(f"  ✗ dot 검증 캡쳐 실패")
                return False
            filled = count_pin_dots(tmp)
            if filled is None:
                print(f"  ⚠ dot 카운트 검출 실패 — 검증 skip (계속 진행)")
            elif filled != i + 1:
                print(f"  ✗ 클릭 {i+1}자리 후 dot={filled} (예상 {i+1}) — abort")
                return False
            else:
                print(f"     ✓ dot {filled}/{len(pin)}")

    print(f"[pin_entry] {card_key} PIN 입력 완료 ✓")
    return True


# CLI — 단일 카드 입력 테스트
def _main():
    import sys
    import os
    from .esp32_client import ESP32Client

    if len(sys.argv) < 3:
        print("usage: python3 -m phone_auto.pin_entry <card_key> <PIN>")
        print(f"available cards: {list(KEYPAD_PRESETS.keys())}")
        sys.exit(1)

    card = sys.argv[1]
    pin = sys.argv[2]
    ip = os.environ.get("ESP32_IP", "172.30.1.17")
    esp = ESP32Client(ip)

    print(f"ESP32 ping...")
    try:
        status = esp.status()
        print(f"  → {status}")
    except Exception as e:
        print(f"  ✗ ESP32 connect 실패: {e}")
        sys.exit(2)

    ok = enter_pin(card, pin, esp)
    sys.exit(0 if ok else 3)


if __name__ == "__main__":
    _main()

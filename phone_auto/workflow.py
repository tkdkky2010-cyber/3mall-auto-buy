"""폰 자율 조작 워크플로 runner.

Step 시퀀스로 카드사 결제 같은 다단계 작업 자동화.

Primitives:
  - tap_text(query, retries=3) — OCR + 좌표 변환 + ESP32 클릭
  - wait_for_text(query, timeout=15) — 텍스트 나타날 때까지 루프
  - tap_xy(x, y, duration_ms=80) — 직접 좌표 (back arrow 등 OCR 어려운 위치)
  - swipe(x1,y1,x2,y2,duration_ms) — 드래그/제스처 (firmware swipe endpoint 필요)
  - enter_pin(card_key, pin) — phone_auto.pin_entry 호출
  - sleep(sec)
  - verify_text(query) — OCR 검증, 없으면 fail
  - back() — phone 화면 좌상단 < 위치 탭 (캘리브 기반 추정)
  - capture(label) — 디버그용 스크린샷 저장

각 step 은 dict 또는 tuple. 실패 시 retry, final fail 시 step 결과 반환.

사용:
    from phone_auto.workflow import Workflow, Step
    from phone_auto.esp32_client import ESP32Client

    esp = ESP32Client('172.30.1.96')
    wf = Workflow(esp, cam_idx=0)
    wf.tap_text("현대카드")
    wf.wait_for_text("결제")
    wf.tap_text("결제")
    wf.enter_pin("hyundai_code7", "1234567")
"""
from __future__ import annotations
import time
from pathlib import Path
from typing import Optional

from .screen_ocr import (
    capture_phone_screen, ocr_text, camera_to_phone,
    find_text, load_calibration,
)


class WorkflowError(Exception):
    pass


class Workflow:
    def __init__(self, esp, cam_idx: int = 0, calib: Optional[dict] = None,
                 step_delay: float = 1.5, verbose: bool = True):
        self.esp = esp
        self.cam_idx = cam_idx
        self.calib = calib or load_calibration()
        if self.calib is None:
            raise WorkflowError("calibration 없음 — screen_ocr.calibrate 필요")
        self.step_delay = step_delay
        self.verbose = verbose
        self.history: list[dict] = []  # 실행 기록

    def _log(self, msg: str):
        if self.verbose:
            print(f"[wf] {msg}")

    def _cap_items(self):
        img = capture_phone_screen(self.cam_idx, warmup_frames=4)
        items = ocr_text(img)
        # 폰 영역 안 텍스트만 (캘리브 bbox 내부)
        tl, br = self.calib["tl"], self.calib["br"]
        x_min = min(tl[0], br[0]) - 10
        x_max = max(tl[0], br[0]) + 10
        y_min = min(tl[1], br[1]) - 10
        y_max = max(tl[1], br[1]) + 10
        return [it for it in items
                if x_min <= it["cx"] <= x_max and y_min <= it["cy"] <= y_max]

    def capture(self, label: str = "") -> list[dict]:
        items = self._cap_items()
        self._log(f"capture {label}: {len(items)}개 텍스트")
        for it in items[:8]:
            self._log(f"  '{it['text']}'  conf={it['conf']:.2f}")
        return items

    def tap_text(self, query: str, retries: int = 2, post_delay: float = None) -> bool:
        delay = self.step_delay if post_delay is None else post_delay
        for attempt in range(retries + 1):
            items = self._cap_items()
            hit = find_text(items, query)
            if hit:
                px, py = camera_to_phone(hit["cx"], hit["cy"], self.calib)
                self._log(f"tap_text '{query}' → '{hit['text']}' phone({px},{py}) conf={hit['conf']:.2f}")
                self.esp.click(px, py)
                time.sleep(delay)
                self.history.append({"action": "tap_text", "query": query, "phone": (px, py)})
                return True
            self._log(f"tap_text '{query}' 못 찾음 (attempt {attempt+1}/{retries+1})")
            if attempt < retries:
                time.sleep(1.5)
        self.history.append({"action": "tap_text", "query": query, "fail": True})
        return False

    def tap_xy(self, x: int, y: int, duration_ms: int = 80, post_delay: float = None) -> None:
        delay = self.step_delay if post_delay is None else post_delay
        self._log(f"tap_xy phone({x},{y}) {duration_ms}ms")
        if duration_ms <= 50:
            self.esp.click(x, y)
        else:
            self.esp.tap(x, y, duration_ms=duration_ms)
        time.sleep(delay)
        self.history.append({"action": "tap_xy", "phone": (x, y)})

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        """ESP32 firmware /swipe endpoint 필요 (5/21 flash 예정)."""
        import requests
        self._log(f"swipe ({x1},{y1}) → ({x2},{y2}) {duration_ms}ms")
        try:
            r = requests.post(f"{self.esp.base_url}/swipe", json={
                "x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration_ms": duration_ms
            }, timeout=5)
            ok = r.status_code == 200
        except Exception as e:
            self._log(f"swipe 실패: {e}")
            ok = False
        time.sleep(self.step_delay)
        self.history.append({"action": "swipe", "args": (x1,y1,x2,y2), "ok": ok})

    def wait_for_text(self, query: str, timeout: float = 15.0,
                      interval: float = 1.0) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            items = self._cap_items()
            if find_text(items, query):
                self._log(f"wait_for_text '{query}' 발견 (after {time.time()-start:.1f}s)")
                return True
            time.sleep(interval)
        self._log(f"wait_for_text '{query}' timeout ({timeout}s)")
        return False

    def verify_text(self, query: str) -> bool:
        items = self._cap_items()
        found = find_text(items, query)
        self._log(f"verify_text '{query}' → {'OK' if found else 'MISSING'}")
        return bool(found)

    def sleep(self, sec: float) -> None:
        self._log(f"sleep {sec}s")
        time.sleep(sec)

    def back_via_swipe(self) -> None:
        """좌측 가장자리에서 오른쪽으로 swipe — Galaxy gesture nav back."""
        self.swipe(5, 1200, 400, 1200, 200)

    def home_via_swipe(self) -> None:
        """화면 하단에서 위로 swipe — gesture nav home."""
        self.swipe(540, 2300, 540, 1200, 250)

    def enter_pin(self, card_key: str, pin: str) -> bool:
        from .pin_entry import enter_pin
        self._log(f"enter_pin {card_key} (PIN {len(pin)}자리)")
        return enter_pin(card_key, pin, self.esp, cam_idx=self.cam_idx)


# ─── 카드사 결제 워크플로 정의 (사용자 결정 사항 - 내일 채워나갈 예정) ───

# 각 워크플로는 Workflow 인스턴스 받아서 단계 실행하는 함수.
# 사용자가 카드사 앱 들어가서 각 단계 라벨 확정해주면 채우기.

def hyundai_payment_template(wf: Workflow, code7: str, pin6: str) -> bool:
    """현대카드 결제 — 사용자 미확정. TEMPLATE only.

    내일 사용자가 알려줄 정보 (앱 안 화면 라벨):
    1. 앱 메인 → "결제" 버튼 텍스트는 무엇? ("간편결제", "QR결제", "Pay" 등)
    2. 결제 시 카드 선택 화면 있나? 어떤 라벨?
    3. 금액 입력 화면 있나? OCR 가능한 입력? 또는 매장 QR 스캔?
    4. 7자리 결제 코드 입력 → keypad "hyundai_code7" 사용
    5. 6자리 비밀번호 → keypad "hyundai_pin6" 사용
    6. 결제 완료 화면 텍스트?
    """
    # 1. 바탕화면 가서 앱 찾기
    if not wf.tap_text("현대카드"):
        raise WorkflowError("바탕화면에 '현대카드' 앱 못 찾음")
    if not wf.wait_for_text("결제", timeout=15):  # 메인 화면 indicator
        raise WorkflowError("현대카드 앱 메인 로딩 실패")
    # 2. 결제 진입
    wf.tap_text("결제")
    # ... 이하 사용자 정의 필요
    return False  # template 미완성


# ─── CLI ───

def _main():
    import sys
    import os
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["explore", "tap", "back", "home", "enter-pin", "capture"])
    p.add_argument("--query")
    p.add_argument("--x", type=int); p.add_argument("--y", type=int)
    p.add_argument("--card"); p.add_argument("--pin")
    p.add_argument("--cam", type=int, default=0)
    p.add_argument("--esp-ip", default=os.environ.get("ESP32_IP", "172.30.1.96"))
    args = p.parse_args()

    from .esp32_client import ESP32Client
    esp = ESP32Client(args.esp_ip)
    wf = Workflow(esp, cam_idx=args.cam)

    if args.cmd == "capture":
        wf.capture("manual")
    elif args.cmd == "tap":
        if args.query:
            ok = wf.tap_text(args.query)
        elif args.x is not None:
            wf.tap_xy(args.x, args.y or 0); ok = True
        else:
            print("--query 또는 --x --y 필요"); sys.exit(1)
        sys.exit(0 if ok else 2)
    elif args.cmd == "back":
        wf.back_via_swipe()
    elif args.cmd == "home":
        wf.home_via_swipe()
    elif args.cmd == "enter-pin":
        if not args.card or not args.pin:
            print("--card --pin 필요"); sys.exit(1)
        ok = wf.enter_pin(args.card, args.pin)
        sys.exit(0 if ok else 2)
    elif args.cmd == "explore":
        # 자율 탐색 데모: capture → 카드사 앱 라벨 찾기 → 탭 → verify
        wf.capture("시작")
        cards = ["현대카드", "하나카드", "KB Pay", "NH pay", "롯데카드", "삼성카드"]
        for card in cards:
            items = wf._cap_items()
            hit = find_text(items, card)
            if hit:
                print(f"\n→ '{card}' 발견. 탭 시도.")
                px, py = camera_to_phone(hit["cx"], hit["cy"], wf.calib)
                wf.tap_xy(px, py, duration_ms=80)
                time.sleep(2)
                wf.capture(f"{card} 탭 후")
                break
        else:
            print("\n바탕화면에 카드앱 없음. 현재 화면이 settings 또는 다른 페이지인 듯.")


if __name__ == "__main__":
    _main()

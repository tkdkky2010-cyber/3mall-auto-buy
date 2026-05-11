"""ESP32-S3 USB HID 컨트롤 클라이언트.

ESP32는 WiFi로 REST API 서버 운영. PC가 HTTP POST 요청으로 click/type 명령 전송.
ESP32는 그 명령을 USB HID 신호로 폰에 전달.

REST API endpoints (ESP32 펌웨어):
- POST /click  body={"x": 100, "y": 200} — 절대 좌표 클릭
- POST /move   body={"x": 100, "y": 200} — 커서 이동만
- POST /type   body={"text": "1234567"} — 키보드 입력 (USB HID Keyboard)
- POST /tap    body={"x": 100, "y": 200, "duration_ms": 50} — 짧은 탭
- GET  /status — ESP32 상태 (BT/USB 연결 등)

좌표계 기준:
- 폰 화면 해상도가 1080x2400이면 (0,0) ~ (1080, 2400)
- USB HID 마우스는 절대 좌표 모드 (absolute positioning) 사용
- 폰이 USB 마우스 절대 좌표 받아서 화면에 매핑
"""
from __future__ import annotations
import time
import requests


class ESP32Client:
    def __init__(self, host: str, port: int = 80, timeout: float = 3.0):
        self.base_url = f"http://{host}:{port}"
        self.timeout = timeout

    def status(self) -> dict:
        r = requests.get(f"{self.base_url}/status", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def click(self, x: int, y: int) -> bool:
        r = requests.post(
            f"{self.base_url}/click", json={"x": x, "y": y}, timeout=self.timeout
        )
        return r.ok

    def tap(self, x: int, y: int, duration_ms: int = 50) -> bool:
        r = requests.post(
            f"{self.base_url}/tap",
            json={"x": x, "y": y, "duration_ms": duration_ms},
            timeout=self.timeout,
        )
        return r.ok

    def type_text(self, text: str) -> bool:
        r = requests.post(
            f"{self.base_url}/type", json={"text": text}, timeout=self.timeout
        )
        return r.ok

    def click_sequence(self, positions: list[tuple[int, int]], delay_ms: int = 150) -> bool:
        """비번 입력처럼 여러 좌표 순서대로 클릭. 클릭 간 delay."""
        for x, y in positions:
            if not self.tap(x, y):
                return False
            time.sleep(delay_ms / 1000)
        return True

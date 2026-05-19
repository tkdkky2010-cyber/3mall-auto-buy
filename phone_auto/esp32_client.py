"""ESP32-S3 USB HID + WiFi REST 클라이언트.

ESP32 가 폰에 USB OTG 로 연결되어 HID 입력장치로 동작.
PC 가 HTTP POST 로 ESP32 에게 좌표 → ESP32 가 폰 화면 클릭.

엔드포인트 (esp32_firmware/main/main.ino 참고):
    GET  /status              → {"wifi":"ok","ip":"..."}
    POST /click  {x, y}       → 좌표 클릭 (단일 탭)
    POST /tap    {x, y, duration_ms}  → 긴 탭
    POST /move   {x, y}       → 커서 이동 (클릭 없이)
    POST /type   {text}       → 텍스트 입력 (HID 키보드)

사용 예:
    from phone_auto.esp32_client import ESP32Client
    esp = ESP32Client('172.30.1.17')
    esp.click(500, 1200)
    esp.click_sequence([(100, 200), (300, 400)], delay_sec=0.3)

CLI:
    python3 phone_auto/esp32_client.py status
    python3 phone_auto/esp32_client.py click 500 1200
    python3 phone_auto/esp32_client.py type "1234"
"""
from __future__ import annotations
import sys
import time
import json
from typing import Iterable

import requests


class ESP32Error(Exception):
    """ESP32 HTTP 호출 실패 (네트워크 / 응답 오류)."""


class ESP32Client:
    """ESP32 펌웨어 HTTP API wrapper.

    Args:
        ip: ESP32 의 IP (예: '172.30.1.17')
        port: HTTP 포트 (펌웨어 default 80)
        timeout: HTTP 요청 timeout (초)
    """

    def __init__(self, ip: str, port: int = 80, timeout: float = 3.0):
        self.base_url = f"http://{ip}:{port}"
        self.timeout = timeout

    def _post(self, path: str, payload: dict) -> dict:
        try:
            r = requests.post(
                f"{self.base_url}{path}",
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise ESP32Error(f"POST {path} 실패: {e}") from e
        if r.status_code != 200:
            raise ESP32Error(f"POST {path} → {r.status_code} {r.text[:200]}")
        try:
            return r.json()
        except json.JSONDecodeError:
            return {"raw": r.text}

    def status(self) -> dict:
        """ESP32 상태 (wifi 연결 + IP). 핑 용도."""
        try:
            r = requests.get(f"{self.base_url}/status", timeout=self.timeout)
        except requests.RequestException as e:
            raise ESP32Error(f"GET /status 실패: {e}") from e
        if r.status_code != 200:
            raise ESP32Error(f"GET /status → {r.status_code}")
        return r.json()

    def click(self, x: int, y: int) -> dict:
        """(x, y) 좌표 단일 클릭."""
        return self._post("/click", {"x": int(x), "y": int(y)})

    def tap(self, x: int, y: int, duration_ms: int = 100) -> dict:
        """(x, y) 좌표 긴 탭 — duration_ms 동안 누르기 (롱프레스 용)."""
        return self._post("/tap", {"x": int(x), "y": int(y), "duration_ms": int(duration_ms)})

    def move(self, x: int, y: int) -> dict:
        """커서를 (x, y) 로 이동 (클릭 X)."""
        return self._post("/move", {"x": int(x), "y": int(y)})

    def type_text(self, text: str) -> dict:
        """HID 키보드로 텍스트 입력."""
        return self._post("/type", {"text": text})

    def click_sequence(
        self,
        coords: Iterable[tuple[int, int]],
        delay_sec: float = 0.3,
        verbose: bool = False,
    ) -> list[dict]:
        """좌표 순차 클릭 + 클릭 간 delay_sec 대기. PIN 입력 등에 사용."""
        results = []
        for i, (x, y) in enumerate(coords):
            if verbose:
                print(f"  [{i+1}] click ({x}, {y})")
            results.append(self.click(x, y))
            if i < len(list(coords)) - 1 if not isinstance(coords, list) else i < len(coords) - 1:
                time.sleep(delay_sec)
            else:
                time.sleep(delay_sec)  # 마지막 클릭 후에도 약간 대기 (다음 단계 안정성)
        return results


# ─── CLI ───

def _main():
    if len(sys.argv) < 2:
        print("usage: python3 phone_auto/esp32_client.py <command> [args]")
        print("commands: status | click X Y | tap X Y [duration_ms] | type TEXT")
        print("env ESP32_IP 으로 IP 지정 (default: 172.30.1.17)")
        sys.exit(1)

    import os
    ip = os.environ.get("ESP32_IP", "172.30.1.17")
    esp = ESP32Client(ip)
    cmd = sys.argv[1]

    if cmd == "status":
        print(json.dumps(esp.status(), indent=2))
    elif cmd == "click":
        x, y = int(sys.argv[2]), int(sys.argv[3])
        print(json.dumps(esp.click(x, y), indent=2))
    elif cmd == "tap":
        x, y = int(sys.argv[2]), int(sys.argv[3])
        dur = int(sys.argv[4]) if len(sys.argv) > 4 else 100
        print(json.dumps(esp.tap(x, y, dur), indent=2))
    elif cmd == "type":
        text = sys.argv[2]
        print(json.dumps(esp.type_text(text), indent=2))
    elif cmd == "smoke":
        # 빠른 확인 — status + 무해한 move 한 번
        print("[1] status:")
        print(json.dumps(esp.status(), indent=2))
        print("[2] move (100, 100):")
        print(json.dumps(esp.move(100, 100), indent=2))
        print("OK")
    else:
        print(f"unknown command: {cmd}")
        sys.exit(2)


if __name__ == "__main__":
    _main()

"""ADB 기반 폰 input — ESP HID 대체.

ESP HID는 X 좌표 매핑 깨짐 확인됨 (2026-05-22). ADB 사용으로 100% 정확.

전제:
  - 폰 USB 디버깅 ON + 무선 디버깅 ON
  - `adb pair <IP:PORT> <CODE>` + `adb connect <IP:PORT>` 완료
  - 폰 IP는 같은 WiFi 대역 (3mall 운영 시 KT_GiGA_8650)

사용 예:
    from phone_auto.adb_input import ADB, load_coords
    adb = ADB()  # 자동 device 선택 (wifi 우선)
    apps = load_coords("home_apps")
    adb.tap(apps["현대카드"]["x"], apps["현대카드"]["y"])
    adb.home()  # KEYCODE_HOME
    adb.back()  # KEYCODE_BACK

CLI:
    python3 -m phone_auto.adb_input devices
    python3 -m phone_auto.adb_input tap 현대카드
    python3 -m phone_auto.adb_input key home
    python3 -m phone_auto.adb_input dump   # 현재 화면 uiautomator dump
"""
from __future__ import annotations
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

COORDS_DIR = Path(__file__).resolve().parent / "coords"


class ADBError(Exception):
    pass


def _run(args: list[str], timeout: float = 10.0) -> str:
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise ADBError(f"{' '.join(args)} → {r.returncode}\n{r.stderr}")
    return r.stdout


def list_devices() -> list[str]:
    out = _run(["adb", "devices"])
    devs = []
    for ln in out.splitlines()[1:]:
        ln = ln.strip()
        if "\tdevice" in ln:
            devs.append(ln.split("\t")[0])
    return devs


def pick_device(prefer_wifi: bool = True) -> str:
    env = os.environ.get("ANDROID_SERIAL")   # 호출측이 고정한 기기 우선 (USB/무선 일관 타겟)
    if env:
        return env
    devs = list_devices()
    if not devs:
        raise ADBError("ADB 연결된 device 없음. `adb devices` 확인")
    if prefer_wifi:
        wifi = [d for d in devs if "_tcp" in d or ":" in d]
        if wifi:
            return wifi[0]
    return devs[0]


class ADB:
    def __init__(self, device: Optional[str] = None):
        self.device = device or pick_device()

    def _shell(self, *args: str, timeout: float = 10.0) -> str:
        return _run(["adb", "-s", self.device, "shell", *args], timeout=timeout)

    # --- input primitives ---
    def tap(self, x: int, y: int) -> None:
        self._shell("input", "tap", str(x), str(y))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self._shell("input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms))

    def long_press(self, x: int, y: int, duration_ms: int = 600) -> None:
        # input swipe with same start/end = long press
        self.swipe(x, y, x, y, duration_ms)

    def keyevent(self, code: str) -> None:
        if not code.startswith("KEYCODE_"):
            code = "KEYCODE_" + code.upper()
        self._shell("input", "keyevent", code)

    def text(self, s: str) -> None:
        safe = s.replace(" ", "%s").replace("'", "\\'")
        self._shell("input", "text", safe)

    def input_pin(self, digits: str, digit_to_xy: dict, delay_sec: float = 0.5) -> None:
        """카드사 PIN/결제코드 입력 — 매 tap 사이 0.5초 wait (메모리 [[card-pin-input-pacing]]).

        Args:
            digits: 입력할 숫자 문자열 (예: "137601")
            digit_to_xy: {'0':(x,y), '1':(x,y), ...} 매핑. 매 입력 시 dump+OCR로 재추출.
            delay_sec: 각 tap 사이 wait. 기본 0.5초. 너무 빠르면 키패드 누락/셔플 충돌.
        """
        import time
        for d in digits:
            xy = digit_to_xy.get(d)
            if not xy:
                raise ValueError(f"digit '{d}' not in keypad layout: {list(digit_to_xy)}")
            self.tap(xy[0], xy[1])
            time.sleep(delay_sec)

    # --- system nav shortcuts ---
    def home(self) -> None:
        self.keyevent("KEYCODE_HOME")

    def back(self) -> None:
        self.keyevent("KEYCODE_BACK")

    def recent_apps(self) -> None:
        self.keyevent("KEYCODE_APP_SWITCH")

    def power(self) -> None:
        self.keyevent("KEYCODE_POWER")

    # --- helpers ---
    def screen_size(self) -> tuple[int, int]:
        out = self._shell("wm", "size")
        # "Physical size: 1080x2400"
        for ln in out.splitlines():
            if "size:" in ln.lower():
                w, h = ln.split(":")[-1].strip().split("x")
                return int(w), int(h)
        raise ADBError(f"size parse fail: {out}")

    def screencap(self, out_path: str) -> None:
        r = subprocess.run(
            ["adb", "-s", self.device, "exec-out", "screencap", "-p"],
            capture_output=True, timeout=15,
        )
        if r.returncode != 0:
            raise ADBError(f"screencap fail: {r.stderr.decode()[:200]}")
        Path(out_path).write_bytes(r.stdout)

    def dump_ui(self, out_path: str) -> None:
        self._shell("uiautomator", "dump", "/sdcard/_uia.xml")
        _run(["adb", "-s", self.device, "pull", "/sdcard/_uia.xml", out_path])


# --- coords loading ---
def load_coords(name: str) -> dict:
    """coords/<name>.json 로드 (e.g. 'home_apps', 'system_nav', 'apps/hyundai_card')."""
    if not name.endswith(".json"):
        name += ".json"
    path = COORDS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"coords not found: {path}")
    return json.loads(path.read_text())


def tap_named(adb: ADB, app_or_button: str, source: str = "home_apps") -> tuple[int,int]:
    """이름으로 검색해서 tap. 예: tap_named(adb, '현대카드', 'home_apps')."""
    data = load_coords(source)
    apps = data.get("apps", data)  # support both shapes
    if app_or_button not in apps:
        raise KeyError(f"'{app_or_button}' not in {source}. available: {list(apps)[:10]}")
    c = apps[app_or_button]
    adb.tap(c["x"], c["y"])
    return c["x"], c["y"]


def _main():
    import sys
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    cmd = args[0]
    if cmd == "devices":
        for d in list_devices():
            print(d)
        return
    adb = ADB()
    print(f"[device] {adb.device}")
    if cmd == "tap":
        name = args[1]
        source = args[2] if len(args) > 2 else "home_apps"
        x, y = tap_named(adb, name, source)
        print(f"[tap] '{name}' @ ({x},{y})")
    elif cmd == "tapxy":
        adb.tap(int(args[1]), int(args[2]))
        print(f"[tap] ({args[1]}, {args[2]})")
    elif cmd == "key":
        adb.keyevent(args[1])
        print(f"[key] {args[1]}")
    elif cmd == "home":
        adb.home()
    elif cmd == "back":
        adb.back()
    elif cmd == "recent":
        adb.recent_apps()
    elif cmd == "dump":
        out = args[1] if len(args)>1 else "/tmp/uia.xml"
        adb.dump_ui(out)
        print(f"[dump] {out}")
    elif cmd == "cap":
        out = args[1] if len(args)>1 else "/tmp/cap.png"
        adb.screencap(out)
        print(f"[cap] {out}")
    elif cmd == "size":
        print(adb.screen_size())
    else:
        print(f"unknown cmd: {cmd}")


if __name__ == "__main__":
    _main()

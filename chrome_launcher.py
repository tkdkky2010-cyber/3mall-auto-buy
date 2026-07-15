"""CDP Chrome 자동 launch 공유 헬퍼.

CDP 포트(9222=Hmall main, 9223=check10) 가 안 떠 있으면 매칭되는 launch 스크립트를 자동 실행.
모든 CDP attach 스크립트에서 connect_over_cdp / debuggerAddress 직전에 한 번 호출.
"""
from __future__ import annotations
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

LAUNCHERS = {
    9222: PROJECT_ROOT / "hsmaster" / "scripts" / "launch-hmall-chrome.sh",
    9223: PROJECT_ROOT / "launch-check10-chrome.sh",
}


def _ensure_tab(endpoint: str) -> None:
    """탭 0개면 빈 탭 1개 생성. (탭 0개 Chrome 은 connect_over_cdp 가
    'Browser context management is not supported' 로 실패 — 2026-06-08.)"""
    try:
        with urllib.request.urlopen(f"{endpoint}/json/list", timeout=2) as r:
            import json as _json
            if _json.load(r):
                return
        urllib.request.urlopen(urllib.request.Request(
            f"{endpoint}/json/new?about:blank", method="PUT"), timeout=3)
    except (urllib.error.URLError, OSError, ValueError):
        pass


def ensure_chrome(port: int = 9222, timeout: int = 30) -> None:
    """CDP {port} 안 떠있으면 launch 스크립트 자동 호출. 떠있으면 탭 ≥1 보장."""
    endpoint = f"http://127.0.0.1:{port}"
    try:
        urllib.request.urlopen(f"{endpoint}/json/version", timeout=1.5)
        _ensure_tab(endpoint)
        return
    except (urllib.error.URLError, OSError):
        pass

    launcher = LAUNCHERS.get(port)
    if launcher is None:
        print(f"[WARN] CDP {port}: 매칭되는 launch 스크립트 없음 (chrome_launcher.LAUNCHERS 에 추가)")
        return
    if not launcher.exists():
        print(f"[WARN] {launcher} 없음 — Chrome 수동 실행 필요")
        return
    print(f"[INFO] CDP {port} 안 떠있음 — {launcher.name} 자동 실행...")
    try:
        subprocess.run(["bash", str(launcher)], check=True, timeout=timeout)
    except subprocess.CalledProcessError as e:
        print(f"[WARN] launch script 실패 (exit {e.returncode})")
    except subprocess.TimeoutExpired:
        print(f"[WARN] launch script timeout ({timeout}s)")

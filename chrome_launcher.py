"""CDP Chrome 자동 launch 공유 헬퍼.

★단일 CFT 원칙(2026-07-16): 모든 자동화 = 로그인된 CFT(구글 Profile 1) **하나만**.
  다른 브라우저/프로필 절대 금지 (창 포커스 강탈 원인).

★포트 체인(2026-07-16): 9222 가 막히면(점유/행) **같은 CFT 프로필을 9223 → 9224 로**
  옮겨 띄운다. resolve_cdp_port() 가 살아있는 포트를 찾거나 체인 순서로 launch 후
  실제 사용할 포트를 반환 — 모든 attach 스크립트는 이 반환 포트를 쓴다.
"""
from __future__ import annotations
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
LAUNCHER = PROJECT_ROOT / "hsmaster" / "scripts" / "launch-hmall-chrome.sh"
PORT_CHAIN = (9222, 9223, 9224)   # 같은 CFT 프로필, 포트만 폴백

# ensure_chrome(port) 하위호환용
LAUNCHERS = {p: LAUNCHER for p in PORT_CHAIN}


def _alive(port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1.5)
        return True
    except (urllib.error.URLError, OSError):
        return False


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


def _launch(port: int, timeout: int = 30) -> bool:
    """launch 스크립트를 지정 포트로 실행. 성공(포트 응답) 여부 반환."""
    if not LAUNCHER.exists():
        print(f"[WARN] {LAUNCHER} 없음 — Chrome 수동 실행 필요")
        return False
    print(f"[INFO] CDP {port} launch 시도 — {LAUNCHER.name}")
    try:
        subprocess.run(["bash", str(LAUNCHER)], check=True, timeout=timeout,
                       env={**os.environ, "HMALL_CDP_PORT": str(port)})
    except subprocess.CalledProcessError as e:
        print(f"[WARN] launch script 실패 (exit {e.returncode})")
    except subprocess.TimeoutExpired:
        print(f"[WARN] launch script timeout ({timeout}s)")
    return _alive(port)


def resolve_cdp_port(preferred: int = 9222) -> int:
    """실제 사용할 CDP 포트 결정 — 9222 막히면 9223 → 9224 (전부 같은 CFT).

    1) 체인 중 이미 살아있는 포트가 있으면 그대로 재사용 (창 새로 안 띄움).
    2) 없으면 체인 순서로 launch 시도, 처음 성공한 포트 반환.
    3) 전부 실패면 preferred 반환 (호출부의 기존 에러 경로가 처리).
    """
    chain = [preferred] + [p for p in PORT_CHAIN if p != preferred]
    for p in chain:
        if _alive(p):
            _ensure_tab(f"http://127.0.0.1:{p}")
            if p != preferred:
                print(f"[INFO] CDP {preferred} 대신 살아있는 {p} 재사용")
            return p
    for p in chain:
        if _launch(p):
            if p != preferred:
                print(f"[INFO] CDP {preferred} launch 실패 → {p} 로 폴백")
            return p
    print(f"[WARN] CDP 체인 {chain} 전부 실패 — {preferred} 반환 (수동 확인 필요)")
    return preferred


def ensure_chrome(port: int = 9222, timeout: int = 30) -> None:
    """(하위호환) CDP {port} 안 떠있으면 launch. 떠있으면 탭 ≥1 보장.
    포트 폴백이 필요한 호출부는 resolve_cdp_port() 사용."""
    if _alive(port):
        _ensure_tab(f"http://127.0.0.1:{port}")
        return
    _launch(port, timeout=timeout)

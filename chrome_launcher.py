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
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
LAUNCHER = PROJECT_ROOT / "hsmaster" / "scripts" / "launch-hmall-chrome.sh"
PORT_CHAIN = (9222, 9223, 9224)   # 같은 CFT 프로필, 포트만 폴백

# ensure_chrome(port) 하위호환용
LAUNCHERS = {p: LAUNCHER for p in PORT_CHAIN}


def _alive(port: int) -> bool:
    """그 포트에 **PC 크롬(CFT)** 이 떠 있는가. 폰 WebView 는 False.

    ★2026-09-01 실사고 — 응답이 있다고 CFT 가 아니다.
      `phone_auto/hmall_webview.LOCAL_PORT` 가 **9223** 이라 폰 결제 중에는
      `adb forward tcp:9223 → localabstract:webview_devtools_remote_<pid>` 가 걸려 있고,
      그 엔드포인트도 `/json/version` 에 **정상 응답한다.** 종전 구현은 그걸 살아있는 CFT 로 보고
      재사용해서, **PC 자동화(담기·적립·주문조회)가 폰 WebView 를 조종할 뻔했다**
      (실측: `buy.py reward 1` 이 9223 에 붙어 180s 타임아웃, 주문조회도 동일).
      적립이 통째로 날아가는데 로그엔 '살아있는 9223 재사용' 만 찍힌다 = 조용한 오작동.

    구분법: 폰 WebView 응답에는 `Android-Package` 가 있고 User-Agent 에 `Android` 가 들어간다.
    """
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1.5) as r:
            import json as _json
            info = _json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return False
    if info.get("Android-Package") or "Android" in (info.get("User-Agent") or ""):
        print(f"[WARN] CDP {port} 는 **폰 WebView**({info.get('Android-Package')}) — CFT 아님, 건너뜀")
        return False
    return True


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


def _windows_cft_bin() -> Path | None:
    """윈도우 CFT 실행파일 탐색. CFT_BIN 환경변수가 1순위(다른 위치에 깔았을 때).

    맥 launcher(.sh) 와 같은 규칙: @puppeteer/browsers 기본 설치 경로에서 최신 버전을 고른다.
    못 찾으면 None → 호출부가 '수동 실행 필요' 안내.
    """
    env_bin = os.environ.get("CFT_BIN", "").strip()
    if env_bin:
        p = Path(env_bin)
        return p if p.exists() else None
    roots = [Path.home() / "ChromeForTesting", Path.home() / "chrome-for-testing"]
    cands: list[Path] = []
    for root in roots:
        if root.exists():
            cands += sorted(root.glob("chrome/win*/chrome-win*/chrome.exe"))
    return cands[-1] if cands else None


def _launch_windows(port: int, timeout: int = 30) -> bool:
    """윈도우: .sh/osascript 를 못 쓰므로 CFT 를 직접 띄운다.
    맥 launcher 와 동일한 플래그(포트/프로필/팝업차단해제)를 준다."""
    exe = _windows_cft_bin()
    if exe is None:
        print("[WARN] 윈도우 CFT 실행파일 미발견 — CFT_BIN 환경변수로 지정하거나 수동 실행 필요\n"
              "       설치: npx -y @puppeteer/browsers install chrome@stable --path %USERPROFILE%\\ChromeForTesting")
        return False
    user_data = os.environ.get("CFT_USER_DATA_DIR", "") or str(
        Path.home() / "AppData" / "Local" / "Google" / "Chrome for Testing" / "User Data")
    print(f"[INFO] CDP {port} launch 시도 — {exe.name} (windows)")
    try:
        subprocess.Popen([str(exe),
                          f"--remote-debugging-port={port}",
                          "--remote-allow-origins=*",
                          f"--user-data-dir={user_data}",
                          "--profile-directory=Profile 1",
                          "--no-first-run", "--no-default-browser-check",
                          "--disable-popup-blocking", "about:blank"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        print(f"[WARN] CFT 실행 실패: {e}")
        return False
    for _ in range(timeout):
        if _alive(port):
            return True
        time.sleep(1)
    return _alive(port)


def _launch(port: int, timeout: int = 30) -> bool:
    """launch 스크립트를 지정 포트로 실행. 성공(포트 응답) 여부 반환.
    ★맥=기존 .sh(무변화) / 윈도우=CFT 직접 실행 (.sh·osascript·pgrep 을 못 쓰므로)."""
    if sys.platform.startswith("win"):
        return _launch_windows(port, timeout=timeout)
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

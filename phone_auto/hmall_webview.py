"""hmall 앱(com.hmallapp) 계정 로그인/로그아웃 + 장바구니 확인.

폰 hmall 앱은 hmall.com 모바일웹을 띄우는 WebView. 로그인 폼은 키보드가 뜨면
페이지가 스크롤돼 좌표가 흔들리고, 비번 특수문자도 adb input text 로 불안정.
→ **폼은 네이티브 탭으로 띄우고(앱이 callbackUrl 붙은 진짜 loginForm 로딩), 입력/클릭은
raw CDP(JS)** 로 한다. WebView devtools 소켓에 adb forward 후 CDP 연결.
Chrome 132+ 는 Origin 헤더가 안 맞으면 ws 403 → suppress_origin 으로 헤더 제거.

검증(2026-05-29): 계정 #1 CDP fill 10/10 → 로그인 성공.

좌표 (Galaxy S21+ 1080x2400, uiautomator dump 실측):
  - 마이페이지 탭   (756, 2176)   bounds[646,2086][866,2266]  ※2266 아래는 제스처바
  - 홈 탭          (108, 2176)
  - 톱니바퀴(설정)  (1008, 314)   content-desc="앱 설정 화면으로 이동"
  - 로그아웃        (940, 343)    설정화면 우측상단 txtLoginOrLogout
  - Hmall 로그인하기 (540, 1310)   로그인/회원가입 chooser 의 "Hmall/H.Point 아이디로 로그인하기"

CLI:
    python3 -m phone_auto.hmall_webview login <idx>   # N번 계정 로그인 (logout→nav→CDP)
    python3 -m phone_auto.hmall_webview logout
    python3 -m phone_auto.hmall_webview cart           # 현재 계정 장바구니 empty 여부
"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

import websocket  # websocket-client

ROOT = Path(__file__).resolve().parent.parent
# adb 위치: ① ADB_BIN 환경변수 → ② PATH 자동검색(맥미니=homebrew /opt, 맥북에어=~/platform-tools) → ③ 폴백
ADB = os.environ.get("ADB_BIN") or shutil.which("adb") or "/Users/jasonkim/platform-tools/adb"
LOCAL_PORT = int(os.environ.get("HMALL_CDP_PORT", "9223"))
CART_URL = "https://www.hmall.com/mo/odb/basktList"
ACCOUNTS_FILE = Path(os.environ.get("HMALL_CONFIG_PATH") or (ROOT / "hmall_config.json"))
HMALL_PKG = "com.hmallapp"

# 좌표
HOME_TAB = (108, 2176)
MYPAGE_TAB = (756, 2176)
GEAR = (1008, 314)
LOGOUT_BTN = (940, 343)
HMALL_LOGIN_LINK = (540, 1310)


# ──────────────────────────── adb ────────────────────────────

def _serial() -> str:
    s = os.environ.get("ANDROID_SERIAL")
    if s:
        return s
    out = subprocess.run([ADB, "devices"], capture_output=True, text=True, timeout=10).stdout
    devs = [ln.split("\t")[0] for ln in out.splitlines()[1:] if "\tdevice" in ln]
    if not devs:
        raise RuntimeError("adb device 없음")
    for d in devs:
        if "_tcp" in d:
            return d
    for d in devs:
        if ":" in d:
            return d
    return devs[0]


def _sh(serial: str, *args: str, timeout: float = 15) -> str:
    return subprocess.run([ADB, "-s", serial, *args], capture_output=True, text=True, timeout=timeout).stdout


def _tap(serial: str, xy: tuple[int, int], wait: float = 1.2) -> None:
    _sh(serial, "shell", "input", "tap", str(xy[0]), str(xy[1]))
    time.sleep(wait)


def _launch(serial: str) -> None:
    _sh(serial, "shell", "monkey", "-p", HMALL_PKG, "-c", "android.intent.category.LAUNCHER", "1")
    time.sleep(5)


def reset_to_main(serial: str) -> None:
    """force-stop → 콜드런치 → 클린 메인 홈 (쿠키=로그인 유지). 꼬인 화면상태 원천 제거."""
    _sh(serial, "shell", "am", "force-stop", HMALL_PKG)
    time.sleep(2)
    _sh(serial, "shell", "monkey", "-p", HMALL_PKG, "-c", "android.intent.category.LAUNCHER", "1")
    time.sleep(8)   # 콜드 스타트 (스플래시 + 메인 로드)


def _dump(serial: str) -> str:
    _sh(serial, "shell", "uiautomator", "dump", "/sdcard/_uia.xml")
    tmp = "/tmp/_hmall_uia.xml"
    _sh(serial, "pull", "/sdcard/_uia.xml", tmp)
    try:
        return Path(tmp).read_text()
    except Exception:
        return ""


# ──────────────────────────── CDP ────────────────────────────

def _webview_socket(serial: str) -> str:
    out = _sh(serial, "shell", "cat", "/proc/net/unix")
    for ln in out.splitlines():
        if "webview_devtools_remote_" in ln:
            return ln.strip().split()[-1].lstrip("@")
    raise RuntimeError("webview_devtools 소켓 없음 — hmall 앱 foreground 확인")


def _forward(serial: str) -> None:
    sock = _webview_socket(serial)
    subprocess.run([ADB, "-s", serial, "forward", f"tcp:{LOCAL_PORT}", f"localabstract:{sock}"],
                   capture_output=True, text=True, timeout=10)


def _http(path: str) -> str:
    return urllib.request.urlopen(f"http://127.0.0.1:{LOCAL_PORT}{path}", timeout=6).read().decode()


class CDP:
    def __init__(self, ws_url: str):
        self.ws = websocket.create_connection(ws_url, timeout=20, suppress_origin=True)
        self._id = 0

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass

    def send(self, method: str, params: dict | None = None, timeout: float = 20):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        end = time.time() + timeout
        while time.time() < end:
            self.ws.settimeout(max(0.1, end - time.time()))
            try:
                msg = json.loads(self.ws.recv())
            except Exception:
                break
            if msg.get("method") == "Page.javascriptDialogOpening":
                self._id += 1
                self.ws.send(json.dumps({"id": self._id, "method": "Page.handleJavaScriptDialog",
                                         "params": {"accept": True}}))
                continue
            if msg.get("id") == mid:
                return msg
        return None

    def ev(self, expr: str, timeout: float = 20):
        r = self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True}, timeout)
        if not r or "result" not in r:
            return None
        return r["result"].get("result", {}).get("value")

    def navigate(self, url: str, settle: float = 2.5):
        self.send("Page.navigate", {"url": url}, timeout=20)
        end = time.time() + 20
        while time.time() < end:
            if self.ev("document.readyState", timeout=5) == "complete":
                break
            time.sleep(0.4)
        time.sleep(settle)


def _page_targets() -> list[dict]:
    return [p for p in json.loads(_http("/json"))
            if p.get("type") == "page" and p.get("webSocketDebuggerUrl")]


def _attach_any() -> CDP:
    """hmall page target 하나에 붙는다 (읽기/네비용)."""
    targets = _page_targets()
    if not targets:
        raise RuntimeError("page target 없음")
    t = next((p for p in targets if "hmall.com" in (p.get("url") or "")), targets[0])
    cdp = CDP(t["webSocketDebuggerUrl"])
    cdp.send("Runtime.enable", timeout=5)
    return cdp


def _attach_login_form() -> CDP | None:
    """input[type=password] 가 있는 page target(로그인 폼)에 붙는다."""
    for p in _page_targets():
        try:
            cdp = CDP(p["webSocketDebuggerUrl"])
            cdp.send("Runtime.enable", timeout=5)
            if cdp.ev("!!document.querySelector('input[type=password]')", timeout=6):
                return cdp
            cdp.close()
        except Exception:
            pass
    return None


def _chooser_loaded() -> bool:
    """로그인/회원가입 chooser 가 떠있나 (CDP — '비회원 주문조회'/'아이디로 로그인' 마커).
    chooser 는 로그아웃 상태에서 마이페이지 탭 시에만 뜨므로 = 신뢰 가능한 로그아웃+nav 판정."""
    for p in _page_targets():
        try:
            cdp = CDP(p["webSocketDebuggerUrl"])
            cdp.send("Runtime.enable", timeout=5)
            b = cdp.ev("document.body?document.body.innerText:''", timeout=5) or ""
            cdp.close()
            if "비회원 주문조회" in b or "아이디로 로그인" in b:
                return True
        except Exception:
            pass
    return False


def _is_mypage_loggedin() -> bool:
    """마이페이지가 '로그인 상태'로 떴나 (CDP body — uiautomator dump 가 네이티브 gear 를
    가끔 못 잡는 불안정성 회피). chooser 마커 있으면 False(로그아웃)."""
    for p in _page_targets():
        try:
            cdp = CDP(p["webSocketDebuggerUrl"])
            cdp.send("Runtime.enable", timeout=5)
            b = cdp.ev("document.body?document.body.innerText:''", timeout=5) or ""
            cdp.close()
            if "비회원 주문조회" in b or "아이디로 로그인" in b:
                return False
            if "로그아웃" in b or "멤버십" in b or ("주문" in b and "배송" in b):
                return True
        except Exception:
            pass
    return False


# 로그인 폼 채우기 + 클릭 (JS — \uXXXX 로 한글 인코딩)
_FILL_JS = (
    "(function(idv,pwv){function setN(el,v){var d=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');"
    "d.set.call(el,v);['input','change','keyup','blur'].forEach(function(t){el.dispatchEvent(new Event(t,{bubbles:true}));});}"
    "var pw=document.querySelector('input[type=password]');if(!pw)return 'NOPW';"
    "var ins=[].slice.call(document.querySelectorAll('input'));"
    "var id=ins.find(function(i){return /\\uc544\\uc774\\ub514|ID/i.test((i.placeholder||'')+(i.getAttribute('aria-label')||''));})"
    "||ins.find(function(i){return ['text','email',''].indexOf(i.type)>=0&&i!==pw;});"
    "if(!id)return 'NOID';id.focus();setN(id,idv);pw.focus();setN(pw,pwv);"
    "return 'FILLED:'+id.value.length+'/'+pw.value.length;})(%ID%,%PW%)"
)
_CLICK_JS = (
    "(function(){var els=[].slice.call(document.querySelectorAll('button,a,input[type=submit],input[type=button]'));"
    "var b=els.find(function(e){return ((e.innerText||e.value||'').trim()).indexOf('\\ub85c\\uadf8\\uc778')>=0;});"
    "if(b){b.click();return 'CLICKED';}var pw=document.querySelector('input[type=password]');"
    "if(pw&&pw.form){pw.form.submit();return 'SUBMIT';}return 'NOBTN';})()"
)


# ──────────────────────────── 동작 ────────────────────────────

def load_accounts() -> list[dict]:
    return json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))["accounts"]


def _logged_in_on_mypage(serial: str) -> bool:
    """마이페이지 탭 누른 직후 dump 로 로그인 여부 판정 (gear/로그아웃 네이티브 노드 존재)."""
    xml = _dump(serial)
    return ("앱 설정 화면으로 이동" in xml) or ("txtLoginOrLogout" in xml)


def logout(serial: str | None = None) -> bool:
    """마이페이지 → 톱니바퀴 → 로그아웃 (네이티브). 검증+재시도. True=로그아웃 완료."""
    serial = serial or _serial()
    _forward(serial)
    for _ in range(3):
        _launch(serial)
        _tap(serial, MYPAGE_TAB, wait=4)
        if _chooser_loaded():
            return True                       # 로그아웃 상태 (chooser) — 확실
        if _is_mypage_loggedin():             # 로그인된 마이페이지 (CDP body 판정)
            _tap(serial, GEAR, wait=3.5)
            _tap(serial, LOGOUT_BTN, wait=3.5)
            _tap(serial, MYPAGE_TAB, wait=4)
            if _chooser_loaded():
                return True
        # 마이페이지 nav 실패(홈에 머묾) → 홈 리셋 후 재시도
        _tap(serial, HOME_TAB, wait=2.5)
    return False


def _open_login_form(serial: str) -> "CDP | None":
    """마이페이지(chooser 확인) → Hmall 로그인하기 → 폼. chooser/폼 확인하며 재시도."""
    _forward(serial)
    for _ in range(4):
        _tap(serial, MYPAGE_TAB, wait=4)
        if _chooser_loaded():                 # chooser 확실히 떴을 때만 링크 탭
            _tap(serial, HMALL_LOGIN_LINK, wait=3)
            for _ in range(6):                # 폼 로드 폴링
                f = _attach_login_form()
                if f:
                    return f
                time.sleep(1.5)
        _tap(serial, HOME_TAB, wait=2.5)      # chooser 미도달 → 홈 리셋 후 재시도
    return None


def login(account_id: str, account_pw: str, serial: str | None = None) -> dict:
    """logout → 홈/마이페이지/Hmall로그인하기 네이티브 nav → CDP 로 폼 채우고 로그인."""
    serial = serial or _serial()
    # 1) 깨끗한 로그아웃 상태로
    if not logout(serial):
        return {"success": False, "error": "logout 실패 (이전 계정 안 풀림)"}
    # 2) 로그인 폼 띄우기 (네이티브, 재시도) → CDP attach
    _launch(serial)
    _tap(serial, HOME_TAB, wait=2.5)
    form = _open_login_form(serial)
    if not form:
        return {"success": False, "error": "로그인 폼(password input) 미발견 — nav 재시도 실패"}
    try:
        fill = _FILL_JS.replace("%ID%", json.dumps(account_id)).replace("%PW%", json.dumps(account_pw))
        fr = form.ev(fill)
        if not (isinstance(fr, str) and fr.startswith("FILLED")):
            return {"success": False, "error": f"fill 실패: {fr}"}
        time.sleep(0.8)
        cr = form.ev(_CLICK_JS)
        if cr not in ("CLICKED", "SUBMIT"):
            return {"success": False, "error": f"로그인 클릭 실패: {cr}"}
        # 4) 검증
        for _ in range(10):
            time.sleep(1.2)
            body = form.ev("document.body?document.body.innerText:''", timeout=6) or ""
            if "로그아웃" in body:
                return {"success": True, "fill": fr}
            if ("일치" in body and "비밀번호" in body) or "로그인에 실패" in body or "다른 로그인" in body:
                return {"success": False, "error": f"차단/실패: {body[:100]}"}
            if form.ev("!document.querySelector('input[type=password]')", timeout=4) and "일시적" not in body and len(body) > 50:
                form.navigate("https://www.hmall.com/mo/mpf/selectMyPageMain")
                b2 = form.ev("document.body?document.body.innerText:''", timeout=6) or ""
                return {"success": "로그아웃" in b2, "via": "mypage_check"}
        return {"success": False, "error": "미확인"}
    finally:
        form.close()


def login_account(idx: int, serial: str | None = None) -> dict:
    serial = serial or _serial()
    acc = load_accounts()[idx - 1]
    r = login(acc["id"], acc["pw"], serial)
    r["idx"] = idx
    r["id"] = acc["id"]
    return r


def cart_state(serial: str | None = None) -> dict:
    """현재 로그인 계정의 장바구니 empty 여부 (CDP). 결제 전 차있나 확인용."""
    serial = serial or _serial()
    _forward(serial)
    cdp = _attach_any()
    try:
        cdp.navigate(CART_URL)
        body = cdp.ev("document.body?document.body.innerText:''") or ""
        empty = ("담긴 상품이 없" in body) or ("장바구니가 비어" in body)
        return {"empty": empty, "body_head": body[:160]}
    finally:
        cdp.close()


def _main():
    import sys
    args = sys.argv[1:]
    serial = _serial()
    print(f"[serial] {serial}")
    if not args:
        print(__doc__); return
    cmd = args[0]
    if cmd == "login":
        print(json.dumps(login_account(int(args[1]), serial), ensure_ascii=False))
    elif cmd == "logout":
        print("logout(was logged in):", logout(serial))
    elif cmd == "cart":
        if len(args) > 1:
            print("login:", login_account(int(args[1]), serial).get("success"))
        print(json.dumps(cart_state(serial), ensure_ascii=False))
    else:
        print(f"unknown cmd: {cmd}")


if __name__ == "__main__":
    _main()

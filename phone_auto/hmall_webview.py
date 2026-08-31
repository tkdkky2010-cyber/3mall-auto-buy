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
#  ⚠️최후수단 좌표 — 1순위는 `_CHOOSER_LINK_JS` CDP 클릭이다(좌표 무관).
#  2026-08-19 실측: 이 버튼은 y≈1454 다. 2026-07-07 주석에 "y1310→1454" 라고 적어놓고
#  **상수를 안 고쳐서** 1310(버튼 위 빈 공간)을 계속 탭하고 있었다 → 로그인 폼 미도달.
#  교훈: 관측을 주석에만 적으면 코드는 안 바뀐다.
HMALL_LOGIN_LINK = (540, 1454)


# ──────────────────────────── adb ────────────────────────────

def _serial() -> str:
    s = os.environ.get("ANDROID_SERIAL")
    if s:
        return s
    out = subprocess.run([ADB, "devices"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10).stdout
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
    return subprocess.run([ADB, "-s", serial, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout).stdout


# ★네비 고정대기 임시 축소용 (사용자 지시 2026-08-26 "오늘만 줄여") — 기본 1.0=종전과 동일.
#   카드앱 PIN 구간과 무관(여기는 몰 네비/콜드런치 대기만). 하한을 둬 0 으로는 못 내려간다.
_NAV_SLEEP_SCALE = float(os.environ.get("HMALL_NAV_SLEEP_SCALE", "1.0"))


def _tap(serial: str, xy: tuple[int, int], wait: float = 1.2) -> None:
    _sh(serial, "shell", "input", "tap", str(xy[0]), str(xy[1]))
    time.sleep(max(0.5, wait * _NAV_SLEEP_SCALE))


def _tap_text(serial: str, needle: str, wait: float = 1.5) -> bool:
    """dump 에서 needle 포함 요소 bounds 중심을 탭 (하드코딩 좌표 드리프트 방지). 못 찾으면 False."""
    import re
    xml = _dump(serial)
    m = re.search(r'text="[^"]*' + re.escape(needle) + r'[^"]*"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
    if not m:
        return False
    x1, y1, x2, y2 = map(int, m.groups())
    _tap(serial, ((x1 + x2) // 2, (y1 + y2) // 2), wait=wait)
    return True


# 홈 광고 모달 닫기 버튼 텍스트 — **재등장 방지 버튼 우선**, 마지막이 단순 '닫기'.
# ★정본 목록: OCR 판독 경로(hmall_hyundai_buy.close_home_popup)도 이걸 import 해 쓴다.
#   목록이 두 군데로 갈라지면 한쪽만 새 팝업 문구를 배워 다른 쪽이 조용히 막힌다.
POPUP_KEYS = ("그만 보기", "오늘 하루", "보지 않기", "닫기")


def close_ad_popup(serial: str | None = None, max_iter: int = 4) -> int:
    """홈 광고 모달 닫기 (uiautomator dump 기반). 닫은 개수 반환.

    ★왜 logout/_open_login_form 안에서 **매번** 부르나 (2026-08-19 실사고):
      `buy_one` 이 로그인 전에 `close_home_popup()` 으로 팝업을 닫아도, `logout()` 은 루프마다
      `_launch()` 로 앱을 다시 앞으로 끌어온다 → **그때 또 다른 팝업이 새로 뜬다.**
      실측: 먼저 '7일간 보지 않기' 를 닫았는데 그 뒤 '오늘의 최저가' + '오늘 그만 보기' 가 떴다.
      팝업이 떠 있으면 마이페이지 탭이 먹지 않아 `logout 실패 (이전 계정 안 풀림)` 로 죽는다
      (계정 #1, 윈도우 첫 폰결제 시도). 로그인 전 1회 닫기로는 부족하다.
    """
    serial = serial or _serial()
    closed = 0
    import re
    for _ in range(max_iter):
        # ★dump 1회로 문구 전부 검사 (2026-08-26): 종전엔 키마다 _tap_text→_dump 를 새로 떠서
        #   팝업이 없어도 dump 4회 ≈ 홈에서 ~15초 멍때림. 탭 좌표 로직은 _tap_text 와 동일.
        xml = _dump(serial)
        hit = None
        for key in POPUP_KEYS:
            m = re.search(r'text="[^"]*' + re.escape(key) + r'[^"]*"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
            if m:
                hit = (key, m)
                break
        if not hit:
            break
        key, m = hit
        x1, y1, x2, y2 = map(int, m.groups())
        _tap(serial, ((x1 + x2) // 2, (y1 + y2) // 2), wait=1.5)
        print(f"   [popup] 광고 닫기 '{key}'", flush=True)
        closed += 1
    return closed


def _launch(serial: str) -> None:
    _sh(serial, "shell", "monkey", "-p", HMALL_PKG, "-c", "android.intent.category.LAUNCHER", "1")
    time.sleep(max(2.0, 5 * _NAV_SLEEP_SCALE))


def reset_to_main(serial: str) -> None:
    """force-stop → 콜드런치 → 클린 메인 홈 (쿠키=로그인 유지). 꼬인 화면상태 원천 제거."""
    _sh(serial, "shell", "am", "force-stop", HMALL_PKG)
    time.sleep(max(1.0, 2 * _NAV_SLEEP_SCALE))
    _sh(serial, "shell", "monkey", "-p", HMALL_PKG, "-c", "android.intent.category.LAUNCHER", "1")
    # ★콜드 스타트: 고정 8s → 소켓 폴링 (2026-08-26). 4s 고정으로 줄였더니 #1 이
    #   'webview_devtools 소켓 없음' → 재시도 'Remote end closed' 로 죽었다(실측).
    #   webview devtools 소켓이 뜰 때까지 기다리면 빠른 날은 빨리, 느린 날은 안 죽는다.
    time.sleep(2.5)                              # 스플래시 최소 안정화 (총 최소 4s — 사용자 지시 2026-08-26)
    # ★2026-08-31: 상한 12s → 120s. 앱 업데이트 안내가 **플레이스토어를 띄워** 포그라운드를
    #   뺏는 날이 있다(실측: 30~70s 동안 com.android.vending, 소켓은 80s 에야 생성).
    #   12s 상한이면 #4·#5 가 'webview_devtools 소켓 없음' 으로 연속 실패한다.
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            _webview_socket(serial)
            break
        except RuntimeError:
            # 플레이스토어가 앞에 있으면 back 으로 빠져나와 앱을 다시 앞으로 보낸다.
            try:
                foc = _sh(serial, "shell", "dumpsys activity activities | grep -m1 topResumedActivity")
                if "com.android.vending" in foc:
                    _sh(serial, "shell", "input", "keyevent", "4")
                    time.sleep(1.2)
                elif HMALL_PKG not in foc:
                    _sh(serial, "shell", "monkey", "-p", HMALL_PKG,
                        "-c", "android.intent.category.LAUNCHER", "1")
                    time.sleep(1.2)
            except Exception:
                pass
            time.sleep(0.7)
    time.sleep(1.5)                              # 소켓 직후 page target 준비 여유 (Remote end closed 방지)


def _dump(serial: str) -> str:
    _sh(serial, "shell", "uiautomator", "dump", "/sdcard/_uia.xml")
    tmp = "/tmp/_hmall_uia.xml"
    _sh(serial, "pull", "/sdcard/_uia.xml", tmp)
    try:
        return Path(tmp).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


# ──────────────────────────── CDP ────────────────────────────

def _webview_socket(serial: str) -> str:
    out = _sh(serial, "shell", "cat", "/proc/net/unix")
    socks = [ln.strip().split()[-1].lstrip("@") for ln in out.splitlines()
             if "webview_devtools_remote_" in ln]
    if not socks:
        raise RuntimeError("webview_devtools 소켓 없음 — hmall 앱 foreground 확인")
    # ★카드앱(KB/삼성 등)이 백그라운드 웹뷰로 devtools 소켓을 동시에 열면(이전 결제 잔여)
    #   첫 매칭이 카드앱 빈 웹뷰라 CDP 타겟 0개 → login 타임아웃(2026-07-15 실측: KB카드 21688).
    #   소켓명 뒤 숫자 = 프로세스 PID → hmall(com.hmallapp) PID 소켓을 우선 선택.
    pids = _sh(serial, "shell", "pidof", HMALL_PKG).strip().split()
    for pid in pids:
        want = f"webview_devtools_remote_{pid}"
        if want in socks:
            return want
    return socks[0]


def _forward(serial: str) -> None:
    sock = _webview_socket(serial)
    subprocess.run([ADB, "-s", serial, "forward", f"tcp:{LOCAL_PORT}", f"localabstract:{sock}"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)


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


def _chooser_cdp() -> "CDP | None":
    """로그인/회원가입 chooser page target 에 붙어서 돌려준다 (없으면 None).
    **호출측이 close 책임.** 판정만 필요하면 `_chooser_loaded()` 를 쓴다."""
    for p in _page_targets():
        try:
            cdp = CDP(p["webSocketDebuggerUrl"])
            cdp.send("Runtime.enable", timeout=5)
            b = cdp.ev("document.body?document.body.innerText:''", timeout=5) or ""
            if "비회원 주문조회" in b or "아이디로 로그인" in b:
                return cdp
            cdp.close()
        except Exception:
            pass
    return None


def _chooser_loaded() -> bool:
    """로그인/회원가입 chooser 가 떠있나 (CDP — '비회원 주문조회'/'아이디로 로그인' 마커).
    chooser 는 로그아웃 상태에서 마이페이지 탭 시에만 뜨므로 = 신뢰 가능한 로그아웃+nav 판정."""
    cdp = _chooser_cdp()
    if cdp is None:
        return False
    cdp.close()
    return True


# chooser 의 'Hmall/H.Point 아이디로 로그인하기' 클릭 — **좌표 무관 1순위 경로**.
# ★2026-08-19 실측: 이건 <a> 가 아니라 <button class="_19wx7e91"> 안의 <p> 다.
#   · 클래스명이 CSS-in-JS 해시(`_1nyufc21m` 등)라 셀렉터로 못 쓴다 → **텍스트로 찾고 closest(button)** 클릭.
#   · 이 페이지는 uiautomator dump 에 **텍스트 노드가 하나도 안 잡힌다**(node 16개, text 속성 0개)
#     → `_tap_text` 가 원리적으로 못 찾는다. 그래서 CDP 가 1순위고 dump/좌표는 폴백이다.
_CHOOSER_LINK_JS = (
    "(function(){var N='\\uc544\\uc774\\ub514\\ub85c \\ub85c\\uadf8\\uc778';"          # '아이디로 로그인'
    "var all=document.querySelectorAll('*');"
    "for(var i=0;i<all.length;i++){var e=all[i],own='';"
    "for(var j=0;j<e.childNodes.length;j++){if(e.childNodes[j].nodeType===3)own+=e.childNodes[j].nodeValue;}"
    "if(own.indexOf(N)>=0){var t=(e.closest?e.closest('button,a,[role=button]'):null)||e;"
    "t.click();return 'CLICKED:'+t.tagName;}}"
    "return 'NOTFOUND';})()"
)


def _is_mypage_loggedin() -> bool:
    """마이페이지가 '로그인 상태'로 떴나 (CDP body — uiautomator dump 가 네이티브 gear 를
    가끔 못 잡는 불안정성 회피). chooser 마커 있으면 False(로그아웃).

    ★**마이페이지 타깃만** 읽는다 (2026-08-19 실사고).
      종전엔 page target 을 전부 훑어 '멤버십' 같은 느슨한 마커로 판정했다. 홈
      (`newHome/index`)에 광고 문구 **'VIP 멤버십 제공'** 이 있어서 팝업 때문에 마이페이지에
      **못 갔는데도 True** 가 떴다 → logout() 이 홈 화면에서 톱니바퀴(1008,314)·로그아웃(940,343)
      좌표를 맹탭하고 실패 → `logout 실패 (이전 계정 안 풀림)`.
      READ_FIRST 「페이지에서 뭘 읽을 땐 반드시 해당 영역으로 스코프를 좁힌다」 의 재현이다.
      마커도 마이페이지 본문에만 있는 것으로 바꿨다(2026-08-19 실측 body).
    """
    for p in _page_targets():
        if "/mpf/" not in (p.get("url") or ""):        # 마이페이지(selectMyPageMain 등) 만
            continue
        try:
            cdp = CDP(p["webSocketDebuggerUrl"])
            cdp.send("Runtime.enable", timeout=5)
            b = cdp.ev("document.body?document.body.innerText:''", timeout=5) or ""
            cdp.close()
            if "비회원 주문조회" in b or "아이디로 로그인" in b:
                return False
            if "나의 등급 및 기본 정보" in b or "주문/배송 현황" in b or "회원정보 관리" in b:
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
# ★클릭 우선순위 (2026-08-26 — '로그인' contains 첫 요소를 누르던 탓에 소셜로그인/헤더 링크
#   같은 엉뚱한 요소를 먼저 잡을 수 있었다. 사용자 "로그인 버튼 잘못 누르는 것 같다" 지적):
#   ① 텍스트 정확일치 '로그인'/'로그인하기' ② pw 와 같은 form 안의 로그인 버튼/submit ③ 종전 contains 폴백
_CLICK_JS = (
    "(function(){function txt(e){return ((e.innerText||e.value||'').trim());}"
    "var els=[].slice.call(document.querySelectorAll('button,a,input[type=submit],input[type=button]'));"
    "var b=els.find(function(e){var t=txt(e);return t==='\\ub85c\\uadf8\\uc778'||t==='\\ub85c\\uadf8\\uc778\\ud558\\uae30';});"
    "var pw=document.querySelector('input[type=password]');"
    "if(!b&&pw&&pw.form){var fb=[].slice.call(pw.form.querySelectorAll('button,input[type=submit],input[type=button]'));"
    "b=fb.find(function(e){return txt(e).indexOf('\\ub85c\\uadf8\\uc778')>=0;})||pw.form.querySelector('input[type=submit]');}"
    "if(!b)b=els.find(function(e){return txt(e).indexOf('\\ub85c\\uadf8\\uc778')>=0;});"
    "if(b){b.click();return 'CLICKED:'+txt(b);}"
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
        close_ad_popup(serial)                # ★팝업이 마이페이지 탭을 막는다 (2026-08-19 실사고)
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
        close_ad_popup(serial)                # ★logout 과 같은 이유 — 팝업이 nav 를 먹는다
        _tap(serial, MYPAGE_TAB, wait=4)
        chooser = _chooser_cdp()              # chooser 확실히 떴을 때만 링크 클릭
        if chooser is not None:
            # ★소셜로그인 추가로 'Hmall/H.Point 아이디로 로그인하기' 버튼이 아래로 밀림(좌표 드리프트).
            #   ① CDP 클릭(좌표 무관, 1순위) → ② dump 탭 → ③ 고정좌표. 2026-08-19 신설:
            #   종전엔 ②③ 뿐이었는데 이 페이지는 dump 에 텍스트가 없고 ③ 좌표가 stale(1310)이라
            #   **둘 다 빗나가** `로그인 폼(password input) 미발견` 으로 죽었다.
            try:
                clicked = chooser.ev(_CHOOSER_LINK_JS, timeout=8)
            except Exception as e:
                clicked = f"ERR:{e}"
            finally:
                chooser.close()
            print(f"   [chooser] 아이디로 로그인 클릭 → {clicked}", flush=True)
            if not (isinstance(clicked, str) and clicked.startswith("CLICKED")):
                if not _tap_text(serial, "아이디로 로그인", wait=3):
                    _tap(serial, HMALL_LOGIN_LINK, wait=3)
            time.sleep(1.0)
            for _ in range(8):                # 폼 로드 폴링 (간격 축소 2026-08-26 — 폼 떠있는데 안 누른다는 지적)
                f = _attach_login_form()
                if f:
                    return f
                time.sleep(0.8)
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
        time.sleep(0.3)
        cr = form.ev(_CLICK_JS)
        if not (isinstance(cr, str) and (cr.startswith("CLICKED") or cr == "SUBMIT")):
            return {"success": False, "error": f"로그인 클릭 실패: {cr}"}
        print(f"   [login] 클릭 → {cr}", flush=True)
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

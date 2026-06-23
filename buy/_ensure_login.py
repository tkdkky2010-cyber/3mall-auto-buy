"""9222 세션을 지정 계정으로 깨끗이 로그인 (robust logout + EHUserId 검증).
사용: python3 _ensure_login.py 2
"""
import sys, json
from urllib.parse import unquote
from pathlib import Path
import run
try:
    from patchright.sync_api import sync_playwright
except ImportError:
    from playwright.sync_api import sync_playwright

idx = int(sys.argv[1])
accounts = json.loads(Path(run.ACCOUNTS_FILE).read_text(encoding="utf-8"))["accounts"]
acct = accounts[idx - 1]

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(run.CDP_ENDPOINT, slow_mo=300)
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    page = ctx.new_page()
    for _ in range(3):
        page.goto("https://www.hmall.com/md/dpl/index", wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        if "로그아웃" not in page.inner_text("body"):
            break
        try:
            page.get_by_role("link", name="로그아웃").first.click(); page.wait_for_timeout(2000)
        except Exception:
            pass
    for d in ("hmall.com", ".hmall.com", "www.hmall.com"):
        try: ctx.clear_cookies(domain=d)
        except Exception: pass
    li = run.login(page, acct["id"], acct["pw"])
    ehid = unquote(next((c["value"] for c in ctx.cookies() if c["name"] == "EHUserId"), "") or "")
    ok = li and ehid.lower() == acct["id"].lower()
    print(f"ensure_login #{idx} 기대={acct['id']} login={li} EHUserId={ehid} → {'OK' if ok else 'FAIL'}")
    page.close()
    sys.exit(0 if ok else 1)

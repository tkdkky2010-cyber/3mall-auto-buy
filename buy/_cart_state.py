"""견고한 로그아웃→로그인 후 장바구니 상태(상품명+수량) 덤프 (읽기전용).
사용: python3 _cart_state.py 1        단일
      python3 _cart_state.py 1-4      범위
"""
import sys, json
from pathlib import Path
import run

try:
    from patchright.sync_api import sync_playwright
except ImportError:
    from playwright.sync_api import sync_playwright

accounts = json.loads(Path(run.ACCOUNTS_FILE).read_text(encoding="utf-8"))["accounts"]

a = sys.argv[1] if len(sys.argv) > 1 else "1"
if "-" in a:
    lo, hi = a.split("-"); idxs = list(range(int(lo), int(hi) + 1))
else:
    idxs = [int(a)]


def robust_logout(page):
    for _ in range(3):
        page.goto("https://www.hmall.com/md/dpl/index", wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        if "로그아웃" not in page.inner_text("body"):
            return True
        try:
            page.get_by_role("link", name="로그아웃").first.click()
            page.wait_for_timeout(2000)
        except Exception as e:
            print(f"    [logout err] {e}")
    page.goto("https://www.hmall.com/md/dpl/index", wait_until="domcontentloaded")
    page.wait_for_timeout(1000)
    return "로그아웃" not in page.inner_text("body")


def dump_cart(page):
    page.goto(run.CART_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3500)
    # body 텍스트 기반 파싱: 각 아이템은 "<NAME> 삭제 <price>원 ... <qty>개" 패턴
    txt = page.inner_text("body")
    import re
    items = []
    # '선택삭제' 이후 ~ '장바구니 이용안내' 이전 구간만
    seg = txt
    if "선택삭제" in seg:
        seg = seg.split("선택삭제", 1)[1]
    if "장바구니 이용안내" in seg:
        seg = seg.split("장바구니 이용안내", 1)[0]
    if "고객님을 위한 맞춤 추천" in seg:
        seg = seg.split("고객님을 위한 맞춤 추천", 1)[0]
    # 아이템 단위: '삭제' 토큰 앞 = 상품명, 뒤쪽에서 'N개'
    parts = re.split(r"\n삭제\n", seg)
    for k in range(len(parts) - 1):
        name = parts[k].strip().split("\n")[-1].strip()
        after = parts[k + 1]
        qm = re.search(r"(\d+)\s*개", after)
        items.append({"name": name[:55], "qty": int(qm.group(1)) if qm else None})
    total = (re.search(r"전체\s*\(?\s*(\d+)", txt) or [None, None])[1]
    return {"total_count": total, "items": items, "empty": ("장바구니가 비어" in txt or "담긴 상품이 없" in txt)}


with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(run.CDP_ENDPOINT, slow_mo=300)
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    for idx in idxs:
        acct = accounts[idx - 1]
        page = ctx.new_page()
        ok_lo = robust_logout(page)
        for domain in ("hmall.com", ".hmall.com", "www.hmall.com"):
            try: ctx.clear_cookies(domain=domain)
            except Exception: pass
        li = run.login(page, acct["id"], acct["pw"])
        ehid = next((c["value"] for c in ctx.cookies() if c["name"] == "EHUserId"), None)
        match = "✓" if (ehid and ehid.lower() == acct["id"].lower()) else "✗MISMATCH"
        cart = dump_cart(page) if li else {"items": [], "total_count": "?", "empty": None}
        print(f"\n#{idx} 기대={acct['id']}  login={li}  EHUserId={ehid} [{match}]")
        print(f"   total={cart['total_count']} empty={cart.get('empty')}")
        for it in cart["items"]:
            print(f"     - {it['name']}  x{it['qty']}")
        page.close()

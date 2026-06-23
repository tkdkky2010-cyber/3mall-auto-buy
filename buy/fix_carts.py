"""1~4번 카트 수정 (1회성).
  reduce  : 스키니랩(28) 수량 2→1 (옵션변경 모달, 장바구니 안 비움)  — #2,3,4
  repop   : 장바구니 비우고 17x2, 28x1, 31x2 새로 담기              — #1
안전장치: 작업 전 EHUserId 쿠키로 신원 검증, 불일치 시 SKIP.
사용: python3 fix_carts.py reduce 2
      python3 fix_carts.py reduce 2-4
      python3 fix_carts.py repop 1
"""
import sys, json
from urllib.parse import unquote
from pathlib import Path
import run

try:
    from patchright.sync_api import sync_playwright
except ImportError:
    from playwright.sync_api import sync_playwright

accounts = json.loads(Path(run.ACCOUNTS_FILE).read_text(encoding="utf-8"))["accounts"]
products = json.loads(Path(run.PRODUCTS_FILE).read_text(encoding="utf-8"))

mode = sys.argv[1]
rng = sys.argv[2]
if "-" in rng:
    lo, hi = rng.split("-"); idxs = list(range(int(lo), int(hi) + 1))
else:
    idxs = [int(rng)]

REPOP_PLAN = [("17", 2), ("28", 1), ("31", 2)]  # #1 재담기
# 계정 2,3,4 목표 수량 (상품명 키워드 → 수량). idempotent 강제.
ENFORCE_TARGETS = [("데이즈온", 2), ("스키니랩", 1), ("닥터린", 2)]


def robust_logout(page):
    for _ in range(3):
        page.goto("https://www.hmall.com/md/dpl/index", wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        if "로그아웃" not in page.inner_text("body"):
            return True
        try:
            page.get_by_role("link", name="로그아웃").first.click(); page.wait_for_timeout(2000)
        except Exception:
            pass
    page.goto("https://www.hmall.com/md/dpl/index", wait_until="domcontentloaded"); page.wait_for_timeout(1000)
    return "로그아웃" not in page.inner_text("body")


def cart_items(page):
    """장바구니 (상품명, 수량) 리스트 + 비었는지."""
    page.goto(run.CART_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3500)
    txt = page.inner_text("body")
    import re
    seg = txt.split("선택삭제", 1)[1] if "선택삭제" in txt else txt
    for cut in ("고객님을 위한 맞춤 추천", "장바구니 이용안내"):
        if cut in seg: seg = seg.split(cut, 1)[0]
    items = []
    parts = re.split(r"\n삭제\n", seg)
    for k in range(len(parts) - 1):
        name = parts[k].strip().split("\n")[-1].strip()
        qm = re.search(r"(\d+)\s*개", parts[k + 1])
        items.append((name[:50], int(qm.group(1)) if qm else None))
    empty = ("장바구니가 비어" in txt or "담긴 상품이 없" in txt)
    return items, empty


def card_qtys(page, needles):
    """장바구니에서 needle별 현재 수량 {needle: qty}. 단일아이템 카드 격리."""
    return page.evaluate(r"""(needles)=>{
        const isOpt=b=>b.tagName==='BUTTON'&&b.textContent.trim()==='옵션변경';
        const cntOpt=el=>[...el.querySelectorAll('button')].filter(isOpt).length;
        const bs=[...document.querySelectorAll('button')].filter(isOpt);
        const res={};
        for(const B of bs){ let card=B; while(card.parentElement && cntOpt(card.parentElement)===1) card=card.parentElement;
            const t=card.textContent; const qm=t.match(/(\d+)\s*개/);
            for(const n of needles){ if(t.includes(n)) res[n]=qm?parseInt(qm[1]):null; } }
        return res;
    }""", needles)


def open_item_modal(page, needle):
    """needle 상품의 단일아이템 카드의 옵션변경 클릭."""
    return page.evaluate(r"""(needle)=>{
        const isOpt=b=>b.tagName==='BUTTON'&&b.textContent.trim()==='옵션변경';
        const cntOpt=el=>[...el.querySelectorAll('button')].filter(isOpt).length;
        const bs=[...document.querySelectorAll('button')].filter(isOpt);
        for(const B of bs){ let card=B; while(card.parentElement && cntOpt(card.parentElement)===1) card=card.parentElement;
            if(card.textContent.includes(needle)){ B.click(); return true; } }
        return false;
    }""", needle)


def _modal_qty(page):
    return page.evaluate(r"""()=>{const i=[...document.querySelectorAll('.layer input[type=number]')].find(x=>x.offsetParent!==null);return i?parseInt(i.value):null;}""")


def set_qty(page, needle, target):
    """needle 상품 수량을 target 으로 (옵션변경 모달, 안 비움). 반환 (ok, msg)."""
    page.goto(run.CART_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3500)
    if not open_item_modal(page, needle):
        return False, f"옵션변경 버튼('{needle}') 못찾음"
    page.wait_for_timeout(2500)
    for _ in range(12):
        val = _modal_qty(page)
        if val is None:
            return False, "모달 수량 input 없음"
        if val == target:
            break
        label = "감소" if val > target else "증가"
        page.locator(".layer button").filter(has_text=label).first.click()
        page.wait_for_timeout(700)
    val = _modal_qty(page)
    if val != target:
        return False, f"수량 {val}≠target {target} — 변경하기 생략"
    page.locator(".layer button.change-btn").filter(has_text="변경하기").first.click()
    page.wait_for_timeout(2500)
    for t in ("확인", "닫기"):
        b = page.locator("button").filter(has_text=t).first
        if b.count() and b.is_visible():
            try: b.click(); page.wait_for_timeout(500)
            except Exception: pass
            break
    return True, "ok"


with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(run.CDP_ENDPOINT, slow_mo=500)
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    for idx in idxs:
        acct = accounts[idx - 1]
        page = ctx.new_page()
        robust_logout(page)
        for d in ("hmall.com", ".hmall.com", "www.hmall.com"):
            try: ctx.clear_cookies(domain=d)
            except Exception: pass
        li = run.login(page, acct["id"], acct["pw"])
        ehid = next((c["value"] for c in ctx.cookies() if c["name"] == "EHUserId"), None)
        ehid_dec = unquote(ehid) if ehid else None
        if not li or not ehid_dec or ehid_dec.lower() != acct["id"].lower():
            print(f"\n#{idx} {acct['id']}: [SKIP] 신원검증 실패 login={li} EHUserId={ehid_dec}")
            page.close(); continue
        print(f"\n#{idx} {acct['id']}: 신원확인 OK (EHUserId={ehid_dec})")
        before, _ = cart_items(page)
        print(f"   before: {before}")

        if mode == "enforce":
            needles = [n for n, _ in ENFORCE_TARGETS]
            cur = card_qtys(page, needles)
            print(f"   current qtys: {cur}")
            for needle, tgt in ENFORCE_TARGETS:
                if cur.get(needle) == tgt:
                    continue
                ok, msg = set_qty(page, needle, tgt)
                print(f"   set {needle}→{tgt}: {ok} ({msg})")
        elif mode == "repop":
            run.clear_cart(page)
            for pid, q in REPOP_PLAN:
                run.add_to_cart(page, pid, products[pid], q)
                page.wait_for_timeout(2000)
        else:
            print("   [ERR] unknown mode"); page.close(); continue

        after, _ = cart_items(page)
        print(f"   after:  {after}")
        page.close()

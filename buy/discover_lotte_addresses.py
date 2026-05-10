#!/usr/bin/env python3
"""19계정의 Lotte 저장 주소 중 키워드 매칭 옵션 찾아 lotte_address_map.json에 저장.

흐름: 계정마다 logout → login → 'b' 1개 cart 추가 → 주문하기 → #base_rmit_nm option 순회 → addr 매칭 → 저장 → logout."""
import json
import re
import sys
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ / "buy"))

from sulwhasoo import (
    sync_playwright, LOTTE_HOME, LOTTE_PRODUCTS,
    pre_logout_mall, lotte_login, lotte_add_product_by_url,
    PlaywrightTimeoutError,
)

CDP = "http://127.0.0.1:9222"
ACCOUNTS_FILE = PROJ / "lotte.json"
OUT = PROJ / "lotte_address_map.json"
KEYWORDS = ["890", "203", "복지문화센터및공영주차장"]


def match_addr(addr: str) -> bool:
    return any(k in addr for k in KEYWORDS)


def discover_one(page, acc_id, acc_pw):
    pre_logout_mall(page, "lotte")
    # pre_logout 후 lotte 페이지 상태에서 login 시도. 안되면 reload.
    if not lotte_login(page, acc_id, acc_pw):
        try:
            page.goto(LOTTE_HOME, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1500)
        except Exception:
            pass
        if not lotte_login(page, acc_id, acc_pw):
            return {"matched": None, "error": "login fail"}

    ctx = page.context
    lps = [p for p in ctx.pages if "lotteimall" in p.url and not p.is_closed()]
    pg = lps[-1] if lps else page

    prod = LOTTE_PRODUCTS["b"]
    try:
        lotte_add_product_by_url(pg, prod["goods_no"], 1)
    except Exception as e:
        return {"matched": None, "error": f"cart add fail: {e}"[:120]}

    pg.goto("https://www.lotteimall.com/cart/searchCartList.lotte", timeout=20000)
    pg.wait_for_load_state("domcontentloaded", timeout=15000)
    pg.wait_for_timeout(1500)
    # 전체선택 (#normalAllChk)
    try:
        pg.locator("#normalAllChk").check(force=True, timeout=5000)
    except Exception:
        pass
    pg.wait_for_timeout(500)
    try:
        pg.get_by_role("link", name=re.compile(r"주문하기")).first.click(timeout=10000)
    except Exception as e:
        return {"matched": None, "error": f"order link click fail: {e}"[:120]}

    pg.wait_for_load_state("domcontentloaded", timeout=20000)
    pg.wait_for_timeout(2500)

    sel = pg.locator("#base_rmit_nm")
    if sel.count() == 0:
        return {"matched": None, "error": "no #base_rmit_nm on order page"}

    opts = sel.evaluate(
        "e => Array.from(e.options).map(o => ({val: o.value, text: o.innerText.trim()}))"
    )
    matched_val = None
    addrs = []
    for o in opts:
        try:
            data = pg.evaluate(
                """async (sn) => {
                    const r = await fetch('/order/searchDelivAddressListAjax.lotte', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                        body: 'dlvp_sn=' + sn
                    });
                    const arr = await r.json();
                    return arr[0] || null;
                }""",
                o["val"],
            )
            if data:
                cur = f"{data.get('POST_ADDR','')} {data.get('DTL_ADDR','')}".strip()
            else:
                cur = ""
        except Exception:
            cur = ""
        addrs.append({"value": o["val"], "label": o["text"], "addr": cur})
        if not matched_val and match_addr(cur):
            matched_val = o["val"]

    return {"matched": matched_val, "addresses": addrs}


def main():
    with ACCOUNTS_FILE.open() as f:
        accs = json.load(f)["accounts"]

    out = {}
    if OUT.exists():
        out = json.loads(OUT.read_text())

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]

        for i, acc in enumerate(accs, 1):
            aid = acc["id"]
            if aid in out and out[aid].get("matched"):
                print(f"[{i}/{len(accs)}] {aid}: skip (matched={out[aid]['matched']})")
                continue
            print(f"[{i}/{len(accs)}] {aid}: discovery start...")

            # 매 계정 fresh 페이지 + 이전 페이지 모두 닫음
            for old in list(ctx.pages):
                try:
                    if not old.is_closed():
                        old.close()
                except Exception:
                    pass
            pg = ctx.new_page()
            time.sleep(2)

            t0 = time.time()
            try:
                result = discover_one(pg, aid, acc["pw"])
            except Exception as e:
                result = {"matched": None, "error": f"exception: {e}"[:120]}
            out[aid] = result
            dt = time.time() - t0
            tag = result.get("matched") or "FAIL"
            print(f"  → {tag} ({dt:.1f}s) addrs={len(result.get('addresses', []))} err={result.get('error', '')}")
            OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2))
            time.sleep(3)  # rate limit 회피

    print(f"\n[DONE] saved → {OUT}")
    matched_n = sum(1 for v in out.values() if v.get("matched"))
    print(f"  matched: {matched_n}/{len(out)}")


if __name__ == "__main__":
    main()

"""롯데 카트 검증 — 계정별로 로그인해서 장바구니에 담긴 조합이 기대와 일치하는지 확인.

goods_no 기반(이름 매칭보다 정확). 불일치면 재담기 대상으로 보고만 함(이 스크립트는 읽기전용).
    python3 buy/_verify_cart.py 15 16 17 18 19 20
"""
import os, sys, json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "buy"))
os.environ.setdefault("CDP_PORT", "9222")

from playwright.sync_api import sync_playwright
import sulwhasoo as sw

# idx -> 기대 조합번호 (1-based). 사용자 확정: 15·16=조합14, 17·18·19·20=조합23
EXPECTED = {15: 14, 16: 14, 17: 23, 18: 23, 19: 23, 20: 23}
GOODS_TO_SKU = {v["goods_no"]: k for k, v in sw.LOTTE_PRODUCTS.items()}


def expected_skuqty(combo_no):
    agg = {}
    for sku, q in sw.COMBOS[combo_no]:
        agg[sku] = agg.get(sku, 0) + q
    return agg


def read_cart(page):
    """카트 페이지에서 (sku->qty), 라인수(Y), 원시정보 추출. goods_no href + 인접 수량."""
    page.get_by_role("link", name="장바구니 장바구니").click(timeout=8000)
    page.wait_for_load_state("domcontentloaded", timeout=12000)
    page.wait_for_timeout(2500)
    info = page.evaluate(r"""() => {
        const txt = document.body.innerText || '';
        const ym = txt.match(/일반\s*\(\d+\/(\d+)\)/);
        const Y = ym ? parseInt(ym[1]) : null;
        // ★카트 라인 = <tr> 안 hidden input (goods_no/goods_nm/ord_qty/nl_prc). ord_qty 기준 순회.
        const out = [];
        const inputs = Array.from(document.querySelectorAll('input[name="ord_qty"]'));
        for (const inp of inputs) {
            const qty = parseInt((inp.value || '0').trim()) || 0;
            const tr = inp.closest('tr');
            let gno = null, name = '', prc = null;
            if (tr) {
                const g = tr.querySelector('input[name="goods_no"]'); if (g) gno = g.value;
                const nm = tr.querySelector('input[name="goods_nm"]'); if (nm) name = nm.value;
                const pr = tr.querySelector('input[name="nl_prc"]'); if (pr) prc = parseInt(pr.value) || null;
            }
            out.push({gno, name, qty, prc});
        }
        return {Y, items: out, raw: txt.replace(/\n{2,}/g,'\n').slice(0, 600)};
    }""")
    return info


def main():
    idxs = [int(x) for x in sys.argv[1:] if x.isdigit()]
    if not idxs:
        print("usage: _verify_cart.py 15 16 ..."); return 1
    sw.ensure_chrome(int(sw.CDP_PORT))
    accounts = sw.load_json(sw.LOTTE_ACCOUNTS)["accounts"]
    results = {}
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(sw.CDP_ENDPOINT)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.pages[-1] if ctx.pages else ctx.new_page()
        for idx in idxs:
            acc = accounts[idx - 1]
            print(f"\n════════ 검증 #{idx} {acc['id']} (기대 조합{EXPECTED.get(idx,'?')}) ════════", flush=True)
            if not sw.lotte_login(page, acc["id"], acc["pw"]):
                print(f"  [LOGIN_FAIL] #{idx} — 검증 불가"); results[idx] = ("LOGIN_FAIL", {}, None); continue
            try:
                info = read_cart(page)
            except Exception as e:
                print(f"  [CART_READ_ERR] {e}"); results[idx] = ("READ_ERR", {}, None); continue
            got = {}
            for it in info["items"]:
                sku = GOODS_TO_SKU.get(it["gno"], f"?{it['gno']}")
                got[sku] = got.get(sku, 0) + (it["qty"] or 0)
                print(f"    line: {it['name']} | goods_no={it['gno']} sku={sku} qty={it['qty']}")
            print(f"    [Y(라인수)={info['Y']}]  집계 got={got}")
            exp = expected_skuqty(EXPECTED[idx])
            ok = (got == exp)
            results[idx] = ("MATCH" if ok else "MISMATCH", got, exp)
            print(f"  → {'✅ MATCH' if ok else '❌ MISMATCH'}  기대={exp} 실제={got}")
            if not ok:
                print(f"    [RAW]\n{info['raw']}")
    print("\n========= 검증 요약 =========")
    for idx in idxs:
        st, got, exp = results.get(idx, ("?", {}, {}))
        print(f"  #{idx} 조합{EXPECTED.get(idx)}: {st}  got={got} exp={exp}")
    bad = [i for i in idxs if results.get(i, ('',))[0] != "MATCH"]
    print(f"\n재담기/조치 필요: {bad if bad else '없음 — 전부 일치'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

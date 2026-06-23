"""ord_qty 행 컨테이너 HTML 덤프 — goods_no/상품명 위치 확정용."""
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "buy"))
os.environ.setdefault("CDP_PORT", "9222")
from playwright.sync_api import sync_playwright
import sulwhasoo as sw

idx = int(sys.argv[1]) if len(sys.argv) > 1 else 15
acc = sw.load_json(sw.LOTTE_ACCOUNTS)["accounts"][idx - 1]
sw.ensure_chrome(int(sw.CDP_PORT))
with sync_playwright() as p:
    b = p.chromium.connect_over_cdp(sw.CDP_ENDPOINT)
    ctx = b.contexts[0]; page = ctx.pages[-1] if ctx.pages else ctx.new_page()
    assert sw.lotte_login(page, acc["id"], acc["pw"]), "login fail"
    page.get_by_role("link", name="장바구니 장바구니").click(timeout=8000)
    page.wait_for_load_state("domcontentloaded"); page.wait_for_timeout(2500)
    d = page.evaluate(r"""() => {
        const inp = document.querySelector('input[name="ord_qty"]');
        if(!inp) return {none:true};
        // 충분히 큰 조상(상품명+goods_no 포함할 행) 까지 올라가서 outerHTML
        let node = inp;
        for(let i=0;i<6 && node.parentElement;i++) node=node.parentElement;
        const html = node.outerHTML;
        // 그 안의 goods_no, data-* 속성, 상품명 후보
        const goods = (html.match(/goods_no=?\D?(\d{6,})/g)||[]).slice(0,6);
        const datas = (html.match(/data-[a-z_]+="[^"]{0,30}"/g)||[]).filter(s=>/goods|prd|item|no/i.test(s)).slice(0,10);
        return {none:false, html: html.slice(0,3000), goods, datas};
    }""")
    if d.get("none"): print("NO ord_qty input"); sys.exit()
    print("=== goods_no 매치 ===", d["goods"])
    print("=== data-* 후보 ===", d["datas"])
    print("\n=== ord_qty 행 HTML (3000) ===")
    print(d["html"])

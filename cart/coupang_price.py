"""쿠팡 가격 조회 — alltimeprice.com 경유 (Cloudflare 우회는 CDP Chrome 재활용).

사용법:
    python3 cart/coupang_price.py "곡물도감 서리태"          # 검색
    python3 cart/coupang_price.py --pid 7290082549-94983617960   # 단일 상품 상세
    python3 cart/coupang_price.py --compare cart/today.json      # hmall today.json 의 22개 상품 일괄

가격 흐름: alltimeprice 페이지를 patchright 로 열어서 SSR HTML 파싱.
JSON API 없음, Cloudflare 차단 우회는 메인 페이지 1회 warmup + 쿠키 재활용.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path
from dotenv import load_dotenv

try:
    from patchright.sync_api import sync_playwright, Page
except ImportError:
    from playwright.sync_api import sync_playwright, Page  # type: ignore

load_dotenv(Path(__file__).parent.parent / "buy" / ".env")
CDP_PORT = os.environ.get("CDP_PORT", "9222")
CDP_ENDPOINT = f"http://127.0.0.1:{CDP_PORT}"

BASE = "https://alltimeprice.com"


def _warmup(page: Page) -> None:
    """Cloudflare 통과를 위해 메인 페이지 1회 진입 (이미 통과되었으면 빠르게 패스)."""
    if "alltimeprice" not in (page.url or ""):
        page.goto(BASE + "/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)


def search(query: str, page: Page, limit: int = 20) -> list[dict]:
    """alltimeprice 검색 → 상품 list 반환.
    각 항목: {productId, vendorItemId, name, current_price, discount_pct,
              discount_amount, is_lowest_ever, alltimeprice_url, coupang_url}
    """
    _warmup(page)
    url = f"{BASE}/search/?search={urllib.parse.quote(query)}"
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2500)
    return page.evaluate(
        """(limit) => {
            const out = [];
            const anchors = Array.from(document.querySelectorAll('a[href*="product/?pid="]'));
            const seen = new Set();
            for (const a of anchors) {
                if (seen.has(a.href)) continue;
                seen.add(a.href);
                // 카드 영역 = '원' 가 포함된 가장 가까운 조상
                let card = a;
                for (let i = 0; i < 4 && card.parentElement; i++) {
                    card = card.parentElement;
                    if (card.textContent.includes('원')) break;
                }
                const raw = card.innerText.replace(/\\s+/g, ' ').trim();
                const pid = a.href.split('pid=')[1] || '';
                const [productId, vendorItemId] = pid.split('-');
                out.push({pid, productId, vendorItemId, href: a.href, raw});
                if (out.length >= limit) break;
            }
            return out;
        }""",
        limit,
    ) or []


def _parse_card(raw: str) -> dict:
    """검색결과 카드 text 파싱.
    예: '역대최저가 9,800원 건강한밥상 국산 서리태, 700g, 1개'
        '51,000원 3% 1,339원 할인 곡물도감 무가당 서리태 가득 콩물두유, 180ml, 30개'
    """
    out = {"current_price": None, "discount_pct": None, "discount_amount": None,
           "is_lowest_ever": False, "is_near_lowest": False, "name": ""}
    text = raw
    if text.startswith("역대최저가"):
        out["is_lowest_ever"] = True
        text = text[len("역대최저가"):].strip()
    elif text.startswith("최저가근접"):
        out["is_near_lowest"] = True
        text = text[len("최저가근접"):].strip()

    pm = re.match(r"([\d,]+)\s*원\s*", text)
    if pm:
        out["current_price"] = int(pm.group(1).replace(",", ""))
        text = text[pm.end():]

    dm = re.match(r"(\d+)\s*%\s*([\d,]+)\s*원\s*할인\s*", text)
    if dm:
        out["discount_pct"] = int(dm.group(1))
        out["discount_amount"] = int(dm.group(2).replace(",", ""))
        text = text[dm.end():]

    out["name"] = text.strip()
    return out


def search_enriched(query: str, page: Page, limit: int = 20) -> list[dict]:
    items = search(query, page, limit=limit)
    out = []
    for it in items:
        parsed = _parse_card(it["raw"])
        out.append({
            "pid": it["pid"],
            "productId": it["productId"],
            "vendorItemId": it["vendorItemId"],
            "alltimeprice_url": it["href"],
            "coupang_url": f"https://www.coupang.com/vp/products/{it['productId']}?vendorItemId={it['vendorItemId']}",
            **parsed,
        })
    return out


def get_detail(pid: str, page: Page) -> dict:
    """상품 상세 페이지 → 현재가/최저가/평균가/history 추출.
    pid 형식: '{productId}-{vendorItemId}'
    """
    _warmup(page)
    url = f"{BASE}/product/?pid={pid}"
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2500)
    return page.evaluate(
        """() => {
            const out = {labels: {}, history: [], body: ''};
            const labels = ['현재가', '최저가', '역대최저가', '평균가', '최고가', '판매가'];
            const all = Array.from(document.querySelectorAll('*'));
            for (const lab of labels) {
                for (const el of all) {
                    if (el.children.length === 0 && el.textContent.trim() === lab) {
                        const p = el.parentElement;
                        if (!p) continue;
                        const txt = (p.innerText || '').slice(0, 150);
                        const m = txt.match(/([\\d,]{3,})\\s*원/);
                        if (m) { out.labels[lab] = parseInt(m[1].replace(/,/g, '')); break; }
                    }
                }
            }
            // 가격 history table
            const tables = document.querySelectorAll('table');
            for (const tbl of tables) {
                const rows = Array.from(tbl.querySelectorAll('tr'));
                const parsed = rows.map(r => Array.from(r.querySelectorAll('th,td'))
                                                  .map(c => c.textContent.trim()));
                if (parsed.length > 1 && parsed[0].some(c => /일|가격|날짜/.test(c))) {
                    out.history = parsed;
                    break;
                }
            }
            out.body = document.body.innerText.slice(0, 500);
            return out;
        }"""
    ) or {}


# ────────────── CLI ──────────────


def _cli_search(query: str) -> int:
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(CDP_ENDPOINT)
        page = b.contexts[0].new_page()
        results = search_enriched(query, page, limit=20)
        page.close()
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\n[OK] {len(results)}개 상품")
    return 0


def _cli_detail(pid: str) -> int:
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(CDP_ENDPOINT)
        page = b.contexts[0].new_page()
        d = get_detail(pid, page)
        page.close()
    print(json.dumps(d, ensure_ascii=False, indent=2))
    return 0


def _cli_compare(today_json_path: str) -> int:
    """hmall today.json 의 10% 상품을 쿠팡 검색 → hmall 실비 vs 쿠팡가 비교 표."""
    data = json.loads(Path(today_json_path).read_text(encoding="utf-8"))
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(CDP_ENDPOINT)
        page = b.contexts[0].new_page()
        results = []
        for prod in data["products"]:
            if prod.get("error") or not prod.get("ten_percent"):
                continue
            if prod.get("alias_of"):
                continue
            name = prod["name"]
            pay = prod.get("payment") or {}
            qty = pay.get("qty") or 1
            kk_final = pay.get("kakao_final_cost")
            print(f"\n[{prod['id']:>3}] {name} 검색중...", flush=True)
            try:
                hits = search_enriched(name, page, limit=5)
            except Exception as e:
                print(f"     [ERR] {e}")
                hits = []
            best = hits[0] if hits else None
            results.append({
                "id": prod["id"],
                "hmall_name": name,
                "hmall_qty": qty,
                "hmall_kakao_final": kk_final,
                "coupang_top_match": (best or {}).get("name"),
                "coupang_price": (best or {}).get("current_price"),
                "coupang_url": (best or {}).get("coupang_url"),
                "is_lowest_ever": (best or {}).get("is_lowest_ever"),
                "discount_pct": (best or {}).get("discount_pct"),
            })
            if best:
                print(f"     → 쿠팡: {best.get('current_price'):,}원  "
                      f"hmall실비(x{qty}): {kk_final or '-'}  "
                      f"매칭: {(best.get('name') or '')[:50]}")
            else:
                print(f"     → 검색결과 없음")
        page.close()
    out_path = Path(today_json_path).parent / "coupang_compare.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[OK] 저장: {out_path}")
    # 표 출력
    print("\n========= hmall 실비 vs 쿠팡 현재가 =========")
    print(f"{'#':>3} | {'hmall 상품':30s} | qty | {'hmall 실비':>9} | {'쿠팡 현재가':>9} | 매칭")
    print("-" * 130)
    for r in results:
        name = (r['hmall_name'][:28] + "…") if len(r['hmall_name']) > 29 else r['hmall_name']
        hf = f"{r['hmall_kakao_final']:,}" if r['hmall_kakao_final'] else "-"
        cp = f"{r['coupang_price']:,}" if r['coupang_price'] else "-"
        match = (r['coupang_top_match'] or '-')[:45]
        print(f"{r['id']:>3} | {name:30s} | {r['hmall_qty']:>3} | {hf:>9} | {cp:>9} | {match}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("query", nargs="?", help="검색어")
    g.add_argument("--pid", help="상품 상세 (productId-vendorItemId)")
    g.add_argument("--compare", help="hmall today.json 경로 — 일괄 비교")
    args = ap.parse_args()
    if args.compare:
        return _cli_compare(args.compare)
    if args.pid:
        return _cli_detail(args.pid)
    return _cli_search(args.query)


if __name__ == "__main__":
    sys.exit(main())

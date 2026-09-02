"""현대몰 카트 **외부 검수** + 쿠폰 확인/수령 (읽기 위주, 담기 안 함).

왜 별도 파일인가 — `buy/run.py` 실행 **중**의 카트 판독은 믿을 수 없다는 결론이 2026-09-02
워크로그에 남아 있었다(실제 원인은 `_JS_CART_ROWS` 의 JS 문법 오류였고 같은 날 고쳤다).
그래도 담기와 검수는 **분리**하는 게 맞다: 담은 세션이 스스로를 검증하면 같은 착각을 공유한다.
→ 계정마다 **쿠키 폐기 + 재로그인** 후 카트를 다시 읽는다.

쿠폰: 사용자 지시 2026-09-02 "뉴케어 구매하기 전에 쿠폰 꼭 받아라 모든계정".
     상품페이지에서 `click_coupon_receive` 를 한 번 더 돌리고 **결과를 계정별로 찍는다**
     (쿠폰은 계정 단위 다운로드라 카트에 담은 뒤 받아도 주문서에서 적용된다).

사용법:
    python buy/verify_cart.py 1-5,11-15            # 카트만 검수
    python buy/verify_cart.py 1-5,11,12 coupon=40  # 카트 검수 + 40번 상품 쿠폰 확인/수령
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import run as R  # noqa: E402  (login/cart/쿠폰 헬퍼 정본 — 여기서 새로 만들지 않는다)

# 카트 행 = 개별상품 체크박스 기준 (하단 '최근 본 상품' 캐러셀이 안 섞인다 — READ_FIRST 규칙).
_JS_ROWS = r"""() => Array.from(document.querySelectorAll('input[type=checkbox][name=backet]'))
    .map(cb => {
        const row = cb.closest('div.pdwrap');
        if (!row) return null;
        const lines = (row.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
        const qline = lines.find(s => /^\d+개$/.test(s));
        return {name: lines[0] || '', qty: qline ? parseInt(qline) : null, lines: lines.slice(0, 6)};
    }).filter(Boolean)"""


def parse_indices(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.extend(range(int(lo), int(hi) + 1))
        else:
            out.append(int(part))
    return out


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    indices = parse_indices(args[0])
    coupon_pids = next(([x for x in a.split("=", 1)[1].split(",") if x]
                        for a in args if a.startswith("coupon=")), [])

    accounts = R.load_json(R.ACCOUNTS_FILE)["accounts"]
    products = R.load_json(R.PRODUCTS_FILE)
    # 기대치 = cart/today_carts.json (담기 계획이 아니라 **결제가 쓸 매니페스트**를 기준으로 본다.
    #   대장·적립이 이 파일을 읽으므로, 카트가 이것과 다르면 그게 곧 사고다.)
    mf = json.loads((ROOT.parent / "cart" / "today_carts.json").read_text(encoding="utf-8"))
    expect = {c["account"]: c["items"] for c in mf.get("carts", [])
              if c.get("mall") in ("현대", "hmall")}

    R.CDP_PORT = str(R.resolve_cdp_port(int(R.CDP_PORT)))
    R.CDP_ENDPOINT = f"http://127.0.0.1:{R.CDP_PORT}"
    print(f"[INFO] CDP endpoint={R.CDP_ENDPOINT}  대상={indices}"
          f"{'  쿠폰=' + ','.join(coupon_pids) if coupon_pids else ''}")

    results: list[tuple[int, str, str, str]] = []
    with R.sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(R.CDP_ENDPOINT, slow_mo=300)
        context = browser.contexts[0]
        page = context.pages[-1] if context.pages else context.new_page()

        for idx in indices:
            acc = accounts[idx - 1]
            print(f"\n{'='*54}\n[검수 #{idx}] {acc['id']}", flush=True)
            R._hmall_clean(context, page, deep=True)     # ★계정 간 상태 차단(한 계정 실패가 뒤를 무너뜨림)
            if not R.login(page, acc["id"], acc["pw"]):
                results.append((idx, acc["id"], "LOGIN_FAIL", "-"))
                print("  [FAIL] 로그인 실패")
                continue

            page.goto(R.CART_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            for _ in range(10):     # ★빈 카트와 '아직 안 그려진 카트'는 화면이 같다 (#1 오판, 9/2)
                if page.evaluate("() => document.querySelectorAll("
                                 "'input[type=checkbox][name=backet]').length") > 0:
                    break
                page.wait_for_timeout(1000)
            rows = page.evaluate(_JS_ROWS) or []
            for r in rows:
                print(f"  [cart] {r['name'][:44]}  x{r['qty']}")

            want = expect.get(idx, [])
            missing = [it for it in want
                       if not any(_match(it["name"], r["name"]) for r in rows)]
            qty_bad = []
            for it in want:
                hit = next((r for r in rows if _match(it["name"], r["name"])), None)
                if hit and hit["qty"] is not None and hit["qty"] != it["qty"]:
                    qty_bad.append(f"{it['name'][:12]} {hit['qty']}≠{it['qty']}")
            if len(rows) != len(want) or missing or qty_bad:
                cart_st = (f"MISMATCH(카트 {len(rows)}건/기대 {len(want)}건"
                           + (f", 누락 {[m['name'][:10] for m in missing]}" if missing else "")
                           + (f", 수량 {qty_bad}" if qty_bad else "") + ")")
                print(f"  ⛔ {cart_st}")
            else:
                cart_st = f"OK({len(rows)}건)"
                print(f"  ✅ 카트 {cart_st}")

            cp_parts = []
            for pid in coupon_pids:
                info = products.get(pid)
                if not info:
                    cp_parts.append(f"{pid}:없는상품")
                    continue
                url = R.ITEM_URL_FMT.format(slitmCd=info["slitmCd"],
                                            extra=info.get("url_extra", ""))
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(2800)
                st = claim_coupons(page, url)   # 열기 → 일괄 다운로드 → 재판독 (한 동작)
                tag = ("판독실패⛔" if st["unknown"]
                       else f"완료{st['done']}" + (f"/미수령{st['pending']}⛔" if st["pending"] else ""))
                cp_parts.append(f"{pid}:{tag}")
                print(f"    [coupon {pid}] 다운완료 {st['done']}장, 미수령 {st['pending']}장"
                      + (f"  적용가 {st['applied']}" if st["applied"] else ""))
            cp_st = " ".join(cp_parts) if cp_parts else "-"
            results.append((idx, acc["id"], cart_st, cp_st))

    print(f"\n{'='*54}\nSUMMARY")
    for idx, aid, cart_st, cp_st in results:
        mark = "✅" if cart_st.startswith("OK") else "⛔"
        print(f"  {mark} #{idx:<3} {aid:<22} 카트={cart_st:<28} 쿠폰={cp_st}")
    bad = [r for r in results if not r[2].startswith("OK") or "⛔" in r[3]]
    print(f"\n  정상 {len(results)-len(bad)}/{len(results)}"
          + (f"  ⛔ 문제 {[r[0] for r in bad]}" if bad else ""))
    return 1 if bad else 0


def claim_coupons(page, url: str) -> dict:
    """`받은 쿠폰` 레이어를 열어 **`쿠폰 일괄 다운로드`** 를 누른다 → 상태 재판독.

    ★왜 `run.click_coupon_receive` 로는 안 되나 (2026-09-02 실측):
      그 함수는 **`쿠폰 받기` 라는 텍스트의 button** 을 찾는데, 현대몰 상품페이지의 실제 버튼은
      `받은 쿠폰`(class `coupon-received`)이고 **받는 행위는 그 안의 `쿠폰 일괄 다운로드`** 다.
      그래서 2번 상품(하루견과 초록)은 쿠폰이 안 받아진 채 `'쿠폰 받기' 버튼 없음 = 쿠폰이 없다`
      로 조용히 통과했다. `cart/today.json` 의 `2: coupon_claimed=false` 가 그 증거였다.
    ★레이어가 **비어서 열릴 때가 있다**(텍스트가 '받은 쿠폰' 한 줄뿐). 그건 '쿠폰 없음'이 아니라
      렌더 실패다. 그리고 한 번 그렇게 되면 **그 뒤 클릭이 전부 타임아웃**한다(레이어가 안 닫힘) →
      Escape 로는 못 풀고 **페이지를 새로 여는 것**만 확실했다."""
    for attempt in range(3):
        if attempt:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
        try:
            btn = page.locator("button", has_text="받은 쿠폰").first
            if not (btn.count() and btn.is_visible()):
                print("    [coupon] '받은 쿠폰' 버튼 없음")
                return {"done": 0, "pending": 0, "applied": "", "unknown": True}
            btn.click(timeout=8000)
            page.wait_for_timeout(2200)
        except Exception as e:
            print(f"    [coupon] 레이어 열기 실패({attempt+1}/3): {str(e)[:60]}")
            continue
        try:
            bulk = page.locator("button:has-text('쿠폰 일괄 다운로드'), "
                                "a:has-text('쿠폰 일괄 다운로드')").first
            if bulk.count() and bulk.is_visible():
                bulk.click(timeout=6000)
                page.wait_for_timeout(2500)
                print("    [coupon] '쿠폰 일괄 다운로드' 클릭함")
                for txt in ("확인", "닫기"):
                    ok = page.locator("button", has_text=txt).first
                    if ok.count() and ok.is_visible():
                        ok.click()
                        page.wait_for_timeout(800)
                        break
        except Exception as e:
            print(f"    [coupon] 일괄 다운로드 실패: {str(e)[:60]}")
        st = coupon_state(page, open_layer=False)
        if not st["unknown"]:
            return st
        print(f"    [coupon] 레이어가 비어서 열림({attempt+1}/3) — 페이지 새로 열고 재시도")
    return {"done": 0, "pending": 0, "applied": "", "unknown": True}


def coupon_state(page, open_layer: bool = True) -> dict:
    """상품페이지 쿠폰 **상태 판독**(읽기 전용). 레이어 열기/수령은 `claim_coupons` 가 한다.

    ★판별 신호 = 쿠폰 행의 `다운완료` / `다운 완료` (2026-09-02 실측).
      쓰면 안 되는 신호 2개 — 둘 다 오판했다:
        · 상품페이지 `받은 쿠폰` 버튼 → 받았든 안 받았든 **항상** 있다(class `coupon-received`).
        · 장바구니 행의 `최대 N원 추가 할인 쿠폰 받기` → **상시 링크**다. 이미 다 받은 계정에도 뜬다.
    ★레이어를 안 열면 상품에 따라 DOM 이 비어 있어 `완료0/미수령0` 이 나온다. 그건 '정상'이 아니라
      **판독 실패(unknown)** 다 — 그대로 통과시키면 '안 받았는데 받았다'가 된다(실측: 2번 상품).
      그래서 열기를 실패하면 unknown 을 그대로 들고 나간다."""
    js = r"""() => {
      const rows = [];
      document.querySelectorAll('div,li,span,button').forEach(el => {
        if (el.children.length > 2) return;
        const t = (el.innerText||'').trim().replace(/\s+/g,' ');
        if (t && t.length <= 70 && /쿠폰|다운/.test(t)) rows.push(t);
      });
      const uniq = Array.from(new Set(rows));
      const applied = uniq.find(t => /쿠폰 적용가 자세히보기 [\d,]+ ?원/.test(t)) || '';
      return {
        done: uniq.filter(t => /^다운\s?완료( -[\d,]+원)?$/.test(t)).length,
        pending: uniq.filter(t => /^다운로드$|^쿠폰 받기$|^다운받기$|^다운$/.test(t)).length,
        applied: (applied.match(/([\d,]+) ?원/) || [,''])[1],
      };
    }"""
    if open_layer:
        try:
            btn = page.locator("button.coupon-received").first
            if btn.count() and btn.is_visible():
                btn.click(timeout=8000)
                page.wait_for_timeout(1800)
        except Exception as e:
            print(f"    [coupon] 레이어 열기 실패: {str(e)[:70]}")
    try:
        st = page.evaluate(js)
    except Exception as e:
        print(f"    [coupon] 상태 판독 실패: {e}")
        return {"done": 0, "pending": 0, "applied": "", "unknown": True}
    st["unknown"] = (st["done"] == 0 and st["pending"] == 0)
    if st["unknown"]:
        print("    [coupon] ⚠️ 쿠폰 행을 한 줄도 못 읽음 — "
              "'받은 것 없음'이 아니라 **판독 실패**로 다룬다")
    return st


def _match(want_name: str, dom_name: str) -> bool:
    """매니페스트 이름 ↔ 실제 상품명 대조.

    ★둘이 **글자 그대로는 다르다** — products.json 의 '하루견과 초록색 100봉' 은 박스 색 통칭이고
      실제 상품명은 '순수견과100% 하루견과 넛츠시그니처 23gx100봉' 이다(9/1 워크로그 §3-3).
      그래서 전체 문자열 비교가 아니라 **구분 가능한 토큰**으로 맞춘다."""
    keys = {"초록": ("넛츠시그니처",), "갈색": ("시그니처오리지널",),
            "이디야": ("이디야",), "뉴케어": ("뉴케어",)}
    for k, toks in keys.items():
        if k in want_name:
            return any(t in dom_name for t in toks)
    return want_name[:6] in dom_name


if __name__ == "__main__":
    sys.exit(main())

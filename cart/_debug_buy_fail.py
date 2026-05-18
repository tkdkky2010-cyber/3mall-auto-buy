"""바로구매 실패 디버그 — 단계별 로깅.

각 단계 (btn-purchase click / 옵션 스크랩 / 옵션 클릭 / 바로구매 click / URL 검사)
에서 실패 위치 + DOM 상태 캡처.

사용: python3 cart/_debug_buy_fail.py <slitmCd> [option_keyword]
예:
    python3 cart/_debug_buy_fail.py 2225431602      # #3 하루견과 갈색
    python3 cart/_debug_buy_fail.py 2227834416      # #4 곡물도감
"""
from __future__ import annotations
import sys, os, re
from pathlib import Path

ROOT = Path(__file__).parent
PROJECT_ROOT = ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(ROOT))

from patchright.sync_api import sync_playwright

if len(sys.argv) < 2:
    print("usage: python3 cart/_debug_buy_fail.py <slitmCd> [option_keyword]")
    sys.exit(1)
slitm = sys.argv[1]
opt_kw = sys.argv[2] if len(sys.argv) > 2 else None
url = f"https://www.hmall.com/md/pda/itemPtc?slitmCd={slitm}&sectId=3059445"

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    print(f"\n[1] navigate: {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=20000)
    page.wait_for_timeout(1500)
    print(f"   url after: {page.url}")

    # 쿠폰 (있으면)
    try:
        for txt in ("쿠폰받기", "전체 쿠폰받기"):
            loc = page.get_by_text(txt).first
            if loc.count() > 0:
                loc.click()
                page.wait_for_timeout(800)
                print(f"   쿠폰 click: {txt}")
                break
    except Exception as e:
        print(f"   쿠폰 click skip: {e}")

    # btn-purchase
    btn = page.locator("button.btn-purchase")
    print(f"\n[2] btn-purchase count={btn.count()}")
    if btn.count() == 0:
        print("   ❌ button.btn-purchase 없음")
        sys.exit(2)
    try:
        btn.first.click()
        page.wait_for_timeout(1500)
        print("   ✓ click 완료")
    except Exception as e:
        print(f"   ❌ click 예외: {e}")
        sys.exit(2)

    # 옵션 패널 스크랩
    print("\n[3] 옵션 스크랩")
    options = page.evaluate("""() => {
        const out = [];
        const links = document.querySelectorAll('a');
        for (const a of links) {
            const text = (a.textContent || '').trim().replace(/\\s+/g, ' ');
            const m = text.match(/\\[선택\\s*(\\d+)\\][^\\d]*?([\\d,]+)\\s*원/);
            if (m) {
                out.push({ idx: parseInt(m[1]), name: text.slice(0, 80), price_raw: m[2] });
            }
        }
        return out;
    }""")
    print(f"   options found: {len(options)}")
    for o in options[:8]:
        print(f"   - [선택{o['idx']}] {o['name']!r}")

    # 옵션 클릭
    print(f"\n[4] 옵션 클릭 (keyword={opt_kw!r})")
    try:
        if opt_kw:
            opt = page.locator("a").filter(has_text=opt_kw).first
        else:
            opt = page.locator("a").filter(has_text=re.compile(r"\[선택\s*1\]")).first
        cnt = opt.count()
        print(f"   match count: {cnt}")
        if cnt > 0:
            opt.click()
            page.wait_for_timeout(700)
            print("   ✓ click")
        else:
            print("   fallback span.choice-num.title")
            sp = page.locator("span.choice-num.title").filter(has_text="[선택 1]").first
            print(f"   span count: {sp.count()}")
            if sp.count() > 0:
                sp.click()
                page.wait_for_timeout(700)
    except Exception as e:
        print(f"   ❌ option click 예외: {e}")

    # 바로구매 버튼 찾기 + 클릭
    print("\n[5] 바로구매 버튼 검색")
    bn_info = page.evaluate("""() => {
        const buttons = Array.from(document.querySelectorAll('button'));
        const visible = buttons.filter(b => b.offsetParent !== null);
        const matches = visible.filter(b => b.textContent.trim() === '바로구매');
        return {
            total_buttons: buttons.length,
            visible: visible.length,
            바로구매_visible: matches.length,
            바로구매_all_text: buttons.filter(b => b.textContent.includes('바로구매')).map(b => ({
                text: b.textContent.trim().slice(0, 30),
                visible: b.offsetParent !== null,
                disabled: b.disabled,
                cls: (b.className || '').slice(0, 80)
            }))
        };
    }""")
    import json
    print(f"   {json.dumps(bn_info, ensure_ascii=False, indent=2)}")

    clicked = page.evaluate("""() => {
        const buyNow = Array.from(document.querySelectorAll('button'))
            .find(b => b.offsetParent !== null && b.textContent.trim() === '바로구매');
        if (buyNow) { buyNow.click(); return true; }
        return false;
    }""")
    print(f"   클릭 결과: {clicked}")
    if not clicked:
        # 다른 패턴 시도
        print("   ❌ '바로구매' 정확 매칭 X. 비슷한 라벨 시도")
        for label in ("구매하기", "주문하기", "결제하기", "장바구니"):
            cnt = page.evaluate(f"""(() => Array.from(document.querySelectorAll('button')).filter(b => b.offsetParent !== null && b.textContent.includes({json.dumps(label)})).length)()""")
            print(f"   '{label}' visible count: {cnt}")
        sys.exit(5)

    # /order 이동 대기
    print("\n[6] /order 이동 대기")
    try:
        page.wait_for_load_state("domcontentloaded", timeout=20000)
    except Exception as e:
        print(f"   wait_for_load_state 타임아웃: {e}")
    page.wait_for_timeout(3500)
    print(f"   final url: {page.url}")
    print(f"   /order in url: {'/order' in (page.url or '')}")

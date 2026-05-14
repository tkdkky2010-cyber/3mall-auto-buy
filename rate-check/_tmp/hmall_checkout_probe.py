"""f×5 체크아웃 페이지의 '카드할인' 섹션 내부 구조 진단.
cart→구매하기 진입 후 카드할인 캐러셀의 카드 후보 모두 추출."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "buy"))
import run as buy_run  # type: ignore
from run import CART_URL, CDP_ENDPOINT, login, clear_cart, add_to_cart  # type: ignore
sys.path.insert(0, str(ROOT / "rate-check"))
import _common as C

IDS = json.load(open(ROOT / "hsmaster" / "config" / "sulwhasoo-ids.json"))["ids"]


def setup_checkout(page):
    page.goto("https://www.hmall.com/mo/mma/myhome", wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    if "loginForm" in page.url:
        cfg = json.load(open(ROOT / "hmall_config.json"))
        acc = cfg["accounts"][0]
        if not login(page, acc["id"], acc["pw"]):
            sys.exit("❌ 로그인 실패")
        page.wait_for_timeout(1500)
    clear_cart(page)
    page.wait_for_timeout(800)
    info = {"name": IDS["f"]["name"], "slitmCd": IDS["f"]["hyundai"],
            "url_extra": "", "option_index": 1, "auto_coupon": True}
    if not add_to_cart(page, "f", info, 5):
        sys.exit("❌ add_to_cart 실패")
    page.goto(CART_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    page.evaluate("""() => {
        const labels = Array.from(document.querySelectorAll('label.chklabel'));
        const t = labels.find(l => l.querySelector('span')?.textContent.trim() === '일반상품');
        if (t) {
            const cb = t.querySelector('input[type="checkbox"]');
            if (cb && !cb.checked) t.click();
        }
    }""")
    page.wait_for_timeout(500)
    btn = page.locator("button.btn-purchase").filter(has_text="구매하기").first
    if btn.count() == 0:
        btn = page.locator("button").filter(has_text="구매하기").first
    btn.click()
    page.wait_for_load_state("domcontentloaded", timeout=15000)
    page.wait_for_timeout(3000)
    print(f"checkout URL: {page.url[:80]}")


def probe(page):
    # 1) 카드할인 헤더 위치
    res = page.evaluate(r"""() => {
        const hdr = Array.from(document.querySelectorAll('.tarvxz0'))
            .find(d => /카드할인/.test(d.textContent));
        if (!hdr) return {error: 'tarvxz0 헤더 없음', cur: location.href};
        const sec = hdr.nextElementSibling;
        if (!sec) return {error: 'next sibling 없음'};
        return {
            hdrTag: hdr.tagName,
            secTag: sec.tagName,
            secCls: sec.className || '',
            secTextLen: sec.textContent.length,
            secText: sec.textContent.replace(/\s+/g,' ').trim(),
        };
    }""")
    print("\n=== hdr & 첫 sibling section ===")
    print(json.dumps(res, ensure_ascii=False, indent=2))

    # 2) 그 section 안의 모든 카드 후보 + 클릭 가능한 요소
    res2 = page.evaluate(r"""() => {
        const hdr = Array.from(document.querySelectorAll('.tarvxz0'))
            .find(d => /카드할인/.test(d.textContent));
        if (!hdr) return {error: 'no hdr'};
        const sec = hdr.nextElementSibling;
        if (!sec) return {error: 'no sec'};

        // section 안 모든 IMG (카드 로고)
        const imgs = Array.from(sec.querySelectorAll('img'));
        const imgsInfo = imgs.map(img => ({
            alt: img.alt,
            src: img.src.slice(-50),
            parentTag: img.parentElement?.tagName,
            parentCls: img.parentElement?.className.slice(0,50),
        }));

        // % 표기된 모든 요소
        const pcts = Array.from(sec.querySelectorAll('*'))
            .filter(e => e.children.length === 0 && /^\d+\s*%$/.test((e.textContent||'').trim()));
        const pctsInfo = pcts.map(e => ({
            pct: e.textContent.trim(),
            tag: e.tagName,
            cls: e.className.slice(0,40),
            parentCls: e.closest('div')?.className?.slice(0,40),
        }));

        // 카드 row 추정 — pct 요소에서 위로 올라가다 알맞은 컨테이너 (img + pct + 카드명 텍스트 단독 포함)
        const rows = pcts.map(pctEl => {
            let row = pctEl;
            for (let lvl=0; lvl<8 && row; lvl++) {
                if (row === sec) break;
                const img = row.querySelector('img[alt]');
                const text = (row.textContent || '').replace(/\s+/g,' ').trim();
                const pctCount = (text.match(/\d+\s*%/g) || []).length;
                if (img && pctCount === 1 && text.length < 120) {
                    return {
                        pct: pctEl.textContent.trim(),
                        tag: row.tagName,
                        cls: row.className.slice(0,80),
                        text,
                        imgAlt: img.alt,
                        clickable: row.onclick !== null ||
                                   row.getAttribute('role') === 'button' ||
                                   row.tabIndex >= 0 ||
                                   row.style.cursor === 'pointer',
                    };
                }
                row = row.parentElement;
            }
            return null;
        }).filter(Boolean);

        // section 자체에 swiper 같은 캐러셀 구조가 있는지
        const swiperEls = sec.querySelectorAll('[class*="swiper"], [class*="slick"], [class*="carousel"]');

        return {
            imgsCount: imgs.length,
            imgsInfo: imgsInfo.slice(0, 10),
            pctsInfo,
            rows,
            swiperCount: swiperEls.length,
        };
    }""")
    print("\n=== section 내부 분석 ===")
    print(json.dumps(res2, ensure_ascii=False, indent=2))


def main():
    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(CDP_ENDPOINT)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        page.set_default_timeout(20000)
        setup_checkout(page)
        probe(page)


if __name__ == "__main__":
    main()

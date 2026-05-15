"""롯데홈쇼핑 설화수 적립 — '구매사은·혜택' 섹션 (#eventBanner) 추출 + 클릭 검증.

1) #eventBanner li.swiper_slide 수집
2) lotte_ignore_keywords.txt negative 매칭 → 1차 제외
3) 남은 항목 data-url 진입 → '구간 적립표' 또는 '최대 N원 적립' 패턴 검증
4) 검증된 항목만 채택 + max_pt fallback (tier_rows 의 max reward)

CLI: 코드 (b~h) 단일 or 'all'.
"""
import time, json, re, sys
from pathlib import Path
import undetected_chromedriver as uc

PROFILE = Path.home() / "LotteSeleniumChrome"
ids = json.load(open(Path(__file__).parent.parent / "hsmaster/config/sulwhasoo-ids.json"))["ids"]
arg = sys.argv[1] if len(sys.argv) > 1 else "g"
codes = list(ids.keys()) if arg == "all" else [arg]

ig_path = Path(__file__).parent.parent / "lotte_ignore_keywords.txt"
IGNORE = [l.strip() for l in ig_path.read_text(encoding="utf-8").splitlines() if l.strip()]
print(f"[INFO] 대상 코드: {codes}")
print(f"[INFO] negative keywords: {IGNORE}")

opts = uc.ChromeOptions()
opts.add_argument(f"--user-data-dir={PROFILE}")
opts.add_argument("--lang=ko-KR")
opts.add_argument("--window-size=1280,900")
driver = uc.Chrome(options=opts, headless=False, use_subprocess=True)


def block_dialogs():
    try:
        driver.execute_script("window.confirm=()=>true;window.alert=()=>{};window.prompt=()=>''")
    except Exception:
        pass


def collect_items():
    return driver.execute_script(r"""
        const banner = document.querySelector('#eventBanner');
        if (!banner) return [];
        const lis = banner.querySelectorAll('li.swiper_slide, li.swiper-slide, li');
        const out = [];
        for (const li of lis) {
            const a = li.querySelector('a[data-url], a[href]');
            const img = li.querySelector('img[alt]');
            const strong = li.querySelector('strong');
            const p = li.querySelector('p');
            const dataUrl = a ? (a.getAttribute('data-url') || a.getAttribute('href')) : null;
            const text = li.textContent.replace(/\s+/g, ' ').trim();
            if (!text || text.length < 3) continue;
            out.push({
                text, alt: img ? img.alt : '',
                title: strong ? strong.textContent.trim() : '',
                subtitle: p ? p.textContent.trim() : '',
                data_url: dataUrl,
            });
        }
        return out;
    """) or []


def verify_event_page():
    return driver.execute_script(r"""
        const body = document.body ? document.body.innerText : '';
        const out = {tier_rows: [], max_pt: null};
        const re1 = /([\d,]{3,})\s*원\s*이상[\s\S]{0,80}?([\d,]{2,})\s*(?:원|P)\s*적립/g;
        let m;
        while ((m = re1.exec(body)) !== null) {
            out.tier_rows.push({min: m[1], reward: m[2]});
            if (out.tier_rows.length >= 20) break;
        }
        const re2 = /최대\s*([\d,]+)\s*(?:원|P)\s*적립/g;
        const maxes = [];
        while ((m = re2.exec(body)) !== null) maxes.push(parseInt(m[1].replace(/,/g, '')));
        if (maxes.length) out.max_pt = Math.max(...maxes);
        out.has_reward_structure = out.tier_rows.length > 0 || out.max_pt !== null;
        const h = document.querySelector('h1, h2, .ttl, .title');
        out.page_title = h ? h.textContent.trim().slice(0, 80) : '';
        out.url = location.href;
        return out;
    """)


def process_one(code: str) -> dict:
    prod = ids[code]
    goods_no = prod["lotte"]
    url = f"https://www.lotteimall.com/goods/viewGoodsDetail.lotte?goods_no={goods_no}"
    print(f"\n{'='*80}\n[{code}] {prod['name']} — goods_no={goods_no}")
    driver.get(url)
    time.sleep(4)
    block_dialogs()
    for y in range(0, 10000, 700):
        driver.execute_script(f"window.scrollTo(0, {y})")
        time.sleep(0.25)
    driver.execute_script("window.scrollTo(0, 0)")
    time.sleep(0.6)

    items = collect_items()
    print(f"  #eventBanner 슬라이드 {len(items)}개")
    for it in items:
        print(f"    · {it['title']} | {it['subtitle']}")

    # 1차 필터
    candidates, ignored = [], []
    for it in items:
        check_text = (it.get("alt") or "") + " " + it.get("text", "")
        hit = [kw for kw in IGNORE if kw in check_text]
        if hit:
            ignored.append({**it, "ignored_by": hit})
            continue
        if re.search(r"최대.*적립", check_text):
            candidates.append(it)

    # 2차 검증 (data-url 진입)
    confirmed = []
    for it in candidates:
        durl = it.get("data_url")
        if not durl:
            continue
        full = durl if durl.startswith("http") else f"https://www.lotteimall.com{durl}"
        print(f"  [검증] {it['title']} → {full[:70]}")
        try:
            driver.get(full)
            time.sleep(3.5)
            block_dialogs()
            for y in range(0, 6000, 600):
                driver.execute_script(f"window.scrollTo(0, {y})")
                time.sleep(0.2)
            driver.execute_script("window.scrollTo(0, 0)")
            time.sleep(0.5)
        except Exception as e:
            print(f"     [ERR navigate] {e}")
            continue
        evt = verify_event_page()
        # fallback: tier_rows 의 max reward 를 max_pt 로
        if not evt.get("max_pt") and evt.get("tier_rows"):
            evt["max_pt"] = max(int(r["reward"].replace(",", "")) for r in evt["tier_rows"])
        print(f"     구간: {evt['tier_rows'][:5]}  max_pt: {evt.get('max_pt')}")
        if evt["has_reward_structure"]:
            confirmed.append({**it, "verification": evt})

    total_max = sum((c["verification"].get("max_pt") or 0) for c in confirmed)
    print(f"  [{code}] 적립 이벤트 {len(confirmed)}건, 합산 최대 적립금: {total_max:,}원")
    return {"product": prod, "items": items, "ignored": ignored,
            "candidates": candidates, "confirmed": confirmed, "total_max": total_max}


all_results = {}
try:
    for code in codes:
        try:
            all_results[code] = process_one(code)
        except Exception as e:
            print(f"  [FATAL {code}] {e}")
            all_results[code] = {"error": str(e)}

    # 요약
    print(f"\n\n{'='*80}\n========= 최종 요약 =========")
    print(f"{'code':4s} | {'상품':36s} | 적립이벤트 | 최대적립금")
    print("-" * 80)
    for code in codes:
        r = all_results.get(code) or {}
        if "error" in r:
            print(f"{code:4s} | ERR {r['error'][:50]}")
            continue
        name = r.get("product", {}).get("name", "")[:34]
        confirmed = r.get("confirmed") or []
        names = " / ".join(c.get("title", "") for c in confirmed) or "-"
        print(f"{code:4s} | {name:36s} | {len(confirmed)}건 | {r.get('total_max', 0):,}원")
        for c in confirmed:
            print(f"     · {c.get('title')} — max {c['verification'].get('max_pt')}")

    # ★ _lotte_reward_dump.json 등 결과파일 절대 X — sheet가 SoT.
    # lotte_all.py는 본 스크립트를 import해서 in-process로 호출 (또는 stdout 파싱).
    # 일관성: stdout에 한 줄 JSON 출력 (다른 프로세스가 읽을 수 있게)
    print("\n=== JSON_DUMP_BEGIN ===")
    print(json.dumps(all_results, ensure_ascii=False))
    print("=== JSON_DUMP_END ===")
finally:
    time.sleep(1)
    driver.quit()

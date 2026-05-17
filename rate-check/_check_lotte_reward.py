"""롯데홈쇼핑 설화수 적립 — '구매사은·혜택' 섹션 (#eventBanner) 추출 + 클릭 검증.

1) #eventBanner li.swiper_slide 수집
2) lotte_ignore_keywords.txt negative 매칭 → 1차 제외
3) 남은 항목 data-url 진입 → '구간 적립표' 또는 '최대 N원 적립' 패턴 검증
4) 검증된 항목 채택. verification.tier_rows = [{min, reward}, ...] (오름차순).
   max_pt = 단일 한도("최대 N원 적립") 또는 tier_rows 최대 reward (fallback).
   ★ lotte.py 는 tier_rows 만 읽음 — max_pt 는 본 스크립트 내부 print 용.

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

        // 모든 임계값 + 모든 적립금 각각 독립 매칭 후 순서대로 페어링.
        // 단일 lazy regex `([N]원 이상 ...{0,80}? [N]원 적립)` 은 두 tier 사이
        // 텍스트가 80자 넘으면 두번째를 놓침 (RULES.md §P8) → 두 패턴 분리로 fix.
        const reThr = /([\d,]{3,})\s*원\s*이상/g;
        const reRwd = /([\d,]{2,})\s*(?:원|P)\s*적립(?!금)/g;  // "N원 적립" — "적립금" 직후 제외
        const reRwdAlt = /적립금\s*([\d,]{2,})\s*원/g;          // "적립금 N원" 변형 패턴

        const thresholds = [];
        let m;
        while ((m = reThr.exec(body)) !== null) {
            const v = parseInt(m[1].replace(/,/g, ''));
            if (v >= 1000) thresholds.push(v);
            if (thresholds.length >= 20) break;
        }
        const rewards = [];
        while ((m = reRwd.exec(body)) !== null) {
            const v = parseInt(m[1].replace(/,/g, ''));
            if (v >= 100) rewards.push(v);
            if (rewards.length >= 20) break;
        }
        while ((m = reRwdAlt.exec(body)) !== null) {
            const v = parseInt(m[1].replace(/,/g, ''));
            if (v >= 100 && !rewards.includes(v)) rewards.push(v);
            if (rewards.length >= 20) break;
        }

        // 순서대로 zip — 두 list 길이가 같아야 정상. 다르면 작은 쪽 길이만큼만.
        const n = Math.min(thresholds.length, rewards.length);
        for (let i = 0; i < n; i++) {
            out.tier_rows.push({min: thresholds[i], reward: rewards[i]});
        }
        // 임계값 오름차순 정렬 (페이지에 거꾸로 나올 가능성 방어)
        out.tier_rows.sort((a, b) => a.min - b.min);

        const re2 = /최대\s*([\d,]+)\s*(?:원|P)\s*적립/g;
        const maxes = [];
        while ((m = re2.exec(body)) !== null) maxes.push(parseInt(m[1].replace(/,/g, '')));
        if (maxes.length) out.max_pt = Math.max(...maxes);
        out.has_reward_structure = out.tier_rows.length > 0 || out.max_pt !== null;
        out.debug = {thr_count: thresholds.length, rwd_count: rewards.length};
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
        # max_pt: 단일 한도 미검출 시 tier_rows 최대 reward 로 fallback (print/요약 용).
        # ★ lotte.py 는 tier_rows 만 사용 — max_pt 는 본 스크립트 내부 표시 전용.
        if not evt.get("max_pt") and evt.get("tier_rows"):
            evt["max_pt"] = max(int(r["reward"]) for r in evt["tier_rows"])
        print(f"     tier_rows: {evt['tier_rows'][:5]}  (max_pt: {evt.get('max_pt')})")
        if evt["has_reward_structure"]:
            confirmed.append({**it, "verification": evt})

    # 이벤트 1회 적용 (RULES §7-3) — 합산이 아니라 이벤트별 최대 reward 표시.
    per_event_max = [(c["verification"].get("max_pt") or 0) for c in confirmed]
    print(f"  [{code}] 적립 이벤트 {len(confirmed)}건, 이벤트별 max: {per_event_max}")
    return {"product": prod, "items": items, "ignored": ignored,
            "candidates": candidates, "confirmed": confirmed,
            "per_event_max": per_event_max}


all_results = {}
try:
    for code in codes:
        try:
            all_results[code] = process_one(code)
        except Exception as e:
            print(f"  [FATAL {code}] {e}")
            all_results[code] = {"error": str(e)}

    # 요약 — tier 리스트 중심 (multi-tier fix 적용 후, RULES §7-2/§7-3)
    print(f"\n\n{'='*80}\n========= 최종 요약 =========")
    print(f"{'code':4s} | {'상품':36s} | 적립이벤트 | tier 리스트")
    print("-" * 80)
    for code in codes:
        r = all_results.get(code) or {}
        if "error" in r:
            print(f"{code:4s} | ERR {r['error'][:50]}")
            continue
        name = r.get("product", {}).get("name", "")[:34]
        confirmed = r.get("confirmed") or []
        print(f"{code:4s} | {name:36s} | {len(confirmed)}건")
        for c in confirmed:
            tr = c["verification"].get("tier_rows") or []
            tr_str = ", ".join(f"{t['min']:,}↑/{t['reward']:,}원" for t in tr) or "(tier 없음)"
            print(f"     · {c.get('title')} — {tr_str}")

    # ★ _lotte_reward_dump.json 등 결과파일 절대 X — sheet가 SoT.
    # lotte.py는 본 스크립트를 subprocess로 호출하고 stdout JSON_DUMP 를 파싱.
    # 일관성: stdout에 한 줄 JSON 출력 (다른 프로세스가 읽을 수 있게)
    print("\n=== JSON_DUMP_BEGIN ===")
    print(json.dumps(all_results, ensure_ascii=False))
    print("=== JSON_DUMP_END ===")
finally:
    time.sleep(1)
    driver.quit()

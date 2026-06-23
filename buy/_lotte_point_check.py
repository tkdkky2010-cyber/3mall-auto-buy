"""롯데 적립금(L.point) 조회 — read-only. lotte.json 계정 로그인 후 마이페이지 포인트 스크랩.

사용:
    python3 buy/_lotte_point_check.py discover 6        # 1계정 마이페이지 덤프(셀렉터 실측용)
    python3 buy/_lotte_point_check.py check 6 11 12 ...  # 지정 계정 L.point 조회

CDP 9222(보이는 Chrome) 사용 — lotte_login 의 캡차 GCV 자동해결 재사용.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "buy"))

from playwright.sync_api import sync_playwright
import sulwhasoo as S

LOTTE_JSON = ROOT / "lotte.json"
# 마이페이지 후보 URL (실측으로 확정)
MYPAGE_CANDIDATES = [
    "https://www.lotteimall.com/member/myPage/forward.MyMain.lotte",
    "https://www.lotteimall.com/mypage/forward.MyMain.lotte",
    "https://www.lotteimall.com/member/myPage/MyMain.lotte",
]


def _accounts() -> list[dict]:
    return json.loads(LOTTE_JSON.read_text(encoding="utf-8"))["accounts"]


def discover(idx: int) -> None:
    accts = _accounts()
    acc = accts[idx - 1]
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = b.contexts[0].new_page()
        ok = S.lotte_login(page, acc["id"], acc["pw"])
        print(f"[#{idx} {acc['id']}] login={ok}")
        if not ok:
            page.close(); return
        # '마이롯데' 클릭 → 실제 마이페이지 도착 URL 확인
        try:
            page.goto(S.LOTTE_HOME, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2500)
            page.get_by_role("link", name="마이롯데").first.click(timeout=5000)
            page.wait_for_timeout(4000)
            print(f"\n*** 마이롯데 클릭 도착: {page.url} | title={page.title()!r} ***")
        except Exception as e:
            print(f"마이롯데 클릭 실패: {e}")
        targets = [page.url]
        for url in targets:
            try:
                page.wait_for_timeout(2000)
                print(f"\n=== {page.url} | title={page.title()!r} ===")
                info = page.evaluate(r"""() => {
                  const out = {hits: [], links: []};
                  const re = /(L\.?\s?POINT|엘포인트|적립금|포인트|마일리지|드림)/i;
                  const els = [...document.querySelectorAll('*')];
                  for (const e of els) {
                    if (e.children.length > 0) continue;
                    const t = (e.innerText||'').trim();
                    if (t && re.test(t) && t.length < 80) {
                      const parent = e.parentElement ? (e.parentElement.innerText||'').replace(/\n/g,' ').trim().slice(0,100) : '';
                      out.hits.push({t, parent});
                    }
                  }
                  // 적립/포인트/마이 관련 링크
                  for (const a of document.querySelectorAll('a')) {
                    const t = (a.innerText||'').trim();
                    const h = a.getAttribute('href')||'';
                    if (/적립|포인트|마이|POINT/i.test(t+h) && t.length<40) out.links.push({t, h: h.slice(0,90)});
                  }
                  out.hits = out.hits.slice(0,40);
                  out.links = out.links.slice(0,25);
                  return out;
                }""")
                for h in info["hits"]:
                    print("  HIT", repr(h["t"]), "| parent:", repr(h["parent"]))
                for l in info["links"]:
                    print("  LINK", repr(l["t"]), "->", l["h"])
            except Exception as e:
                print(f"  {url}: ERR {e}")
        page.close()


def check(idxs: list[int]) -> None:
    accts = _accounts()
    results = {}
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        ctx = b.contexts[0]
        for idx in idxs:
            acc = accts[idx - 1]
            page = ctx.new_page()
            try:
                ok = S.lotte_login(page, acc["id"], acc["pw"])
                if not ok:
                    results[idx] = (acc["id"], "LOGIN_FAIL")
                    print(f"[#{idx} {acc['id']}] LOGIN_FAIL", flush=True)
                    page.close(); continue
                pt = read_point(page)
                results[idx] = (acc["id"], pt)
                print(f"[#{idx} {acc['id']}] L.point = {pt}", flush=True)
            except Exception as e:
                results[idx] = (acc["id"], f"ERR:{e}")
                print(f"[#{idx} {acc['id']}] ERR {e}", flush=True)
            finally:
                try: page.close()
                except Exception: pass
    print("\n===== L.point 요약 =====", flush=True)
    for idx in idxs:
        cid, pt = results.get(idx, ("?", "?"))
        print(f"  #{idx:>2} {cid:<28} {pt}", flush=True)


def read_point(page) -> str:
    """마이롯데(searchMyLotteMain) 에서 적립금(원) + L.POINT 추출.
    적립금 = 롯데홈쇼핑 적립금(결제 사용), L.POINT = 롯데멤버스 통합포인트."""
    try:
        page.goto(S.LOTTE_HOME, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2000)
        page.get_by_role("link", name="마이롯데").first.click(timeout=6000)
        # 마이롯데 도착 + 적립금 블록(원 숫자) 로드까지 대기
        try:
            page.wait_for_url("**/searchMyLotteMain*", timeout=10000)
        except Exception:
            pass
        page.wait_for_timeout(2000)
        try:
            page.wait_for_function(
                "() => /적립금[\\s\\S]{0,40}?[\\d,]{1,9}\\s*원/.test(document.body.innerText)",
                timeout=8000)
        except Exception:
            page.wait_for_timeout(3000)
    except Exception as e:
        return f"마이롯데 진입실패:{e}"
    try:
        val = page.evaluate(r"""() => {
          const body = (document.body.innerText||'').replace(/ /g,' ');
          const out = {};
          // '적립금' 다음 가장 가까운 'N원'
          let m = body.match(/적립금[\s\S]{0,40}?([\d,]{2,9})\s*원/);
          if (m) out.jeokrip = m[1].replace(/,/g,'');
          // 'L.POINT' 다음 가장 가까운 숫자(원/P/점 suffix 필수 — stray 숫자 방지)
          m = body.match(/L\.?\s?POINT[\s\S]{0,40}?([\d,]{1,9})\s*(원|P|점)/i);
          if (m) out.lpoint = m[1].replace(/,/g,'');
          return out;
        }""")
        jp = val.get("jeokrip")
        lp = val.get("lpoint")
        return f"적립금={jp if jp is not None else '?'}원 / L.POINT={lp if lp is not None else '?'}"
    except Exception as e:
        return f"파싱실패:{e}"


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "discover"
    if mode == "discover":
        discover(int(sys.argv[2]))
    elif mode == "check":
        check([int(x) for x in sys.argv[2:]])
    else:
        print(__doc__)

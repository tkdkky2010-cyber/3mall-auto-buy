"""Hmall H.Point 잔액 조회 — read-only. hmall_config.json 전 계정 로그인 후 myhome 포인트 스크랩.

사용:
    python3 buy/_hmall_point_check.py discover 1      # 1계정 myhome 덤프(셀렉터 실측용)
    python3 buy/_hmall_point_check.py check           # 전 계정 H.Point 조회
    python3 buy/_hmall_point_check.py check 1 2 5      # 지정 계정만

CDP 9222 사용. login() = buy/run.py 재사용.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "buy"))

from playwright.sync_api import sync_playwright
from run import CDP_ENDPOINT, login, logout_if_needed  # type: ignore

CFG = ROOT / "hmall_config.json"
MYHOME = "https://www.hmall.com/mo/mpf/selectMyPageMain"


def _accounts() -> list[dict]:
    return json.loads(CFG.read_text(encoding="utf-8"))["accounts"]


def discover(idx: int) -> None:
    acc = _accounts()[idx - 1]
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(CDP_ENDPOINT)
        page = b.contexts[0].new_page()
        logout_if_needed(page)
        ok = login(page, acc["id"], acc["pw"])
        print(f"[#{idx} {acc['id']}] login={ok}")
        if not ok:
            page.close(); return
        target = sys.argv[3] if len(sys.argv) > 3 else "https://www.hmall.com/md/dpl/index"
        page.goto(target, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        print(f"=== {page.url} | title={page.title()!r} ===")
        info = page.evaluate(r"""() => {
          const out = {hits: [], links: []};
          const re = /(H\.?\s?POINT|포인트|적립금|마일리지|예치금|머니|원|P)/i;
          for (const e of document.querySelectorAll('*')) {
            if (e.children.length > 0) continue;
            const t = (e.innerText||'').trim();
            if (t && re.test(t) && t.length < 60) {
              const parent = e.parentElement ? (e.parentElement.innerText||'').replace(/\n/g,' ').trim().slice(0,90) : '';
              out.hits.push({t, parent});
            }
          }
          for (const a of document.querySelectorAll('a')) {
            const t = (a.innerText||'').trim();
            const h = a.getAttribute('href')||'';
            if (/포인트|적립|POINT/i.test(t+h) && t.length<40) out.links.push({t, h: h.slice(0,90)});
          }
          out.hits = out.hits.slice(0,60);
          out.links = out.links.slice(0,25);
          return out;
        }""")
        for h in info["hits"]:
            print("  HIT", repr(h["t"]), "| parent:", repr(h["parent"]))
        for l in info["links"]:
            print("  LINK", repr(l["t"]), "->", l["h"])
        page.close()


def read_point(page) -> str:
    page.goto(MYHOME, wait_until="domcontentloaded")
    page.wait_for_timeout(2000)
    try:
        page.wait_for_function(
            "() => /포인트[\\s\\S]{0,12}?[\\d,]{1,9}\\s*P/.test(document.body.innerText)",
            timeout=8000)
    except Exception:
        page.wait_for_timeout(2000)
    val = page.evaluate(r"""() => {
      const body = (document.body.innerText||'');
      const out = {};
      let m = body.match(/포인트[\s\S]{0,12}?([\d,]{1,9})\s*P/);
      if (m) out.point = m[1].replace(/,/g,'');
      return out;
    }""")
    pt = val.get("point")
    return f"포인트={pt if pt is not None else '?'}P"


def dismiss_pw_campaign(page) -> bool:
    """로그인 후 '비밀번호 변경 캠페인'(pwdChangePup) 인터스티셜이 뜨면
    '90일 후 변경하기'로 닫는다. 닫았으면 True (로그인 자체는 이미 성공한 상태)."""
    try:
        if "pwdChangePup" not in page.url and "비밀번호 변경" not in page.inner_text("body"):
            return False
        clicked = page.evaluate("""() => {
            const els = Array.from(document.querySelectorAll('a, button'));
            const t = els.find(el => /90일\\s*후\\s*변경/.test((el.innerText||'').trim()));
            if (t) { t.click(); return true; }
            return false;
        }""")
        page.wait_for_timeout(1500)
        return bool(clicked)
    except Exception:
        return False


def check(idxs: list[int]) -> None:
    accts = _accounts()
    results = {}
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(CDP_ENDPOINT)
        ctx = b.contexts[0]
        for idx in idxs:
            acc = accts[idx - 1]
            page = ctx.new_page()
            try:
                logout_if_needed(page)
                ok = login(page, acc["id"], acc["pw"])
                if not ok and dismiss_pw_campaign(page):
                    print(f"[#{idx} {acc['id']}] 비밀번호 변경 캠페인 닫음 → 진행", flush=True)
                    ok = True
                if not ok:
                    results[idx] = (acc["id"], "LOGIN_FAIL")
                    print(f"[#{idx} {acc['id']}] LOGIN_FAIL", flush=True)
                    page.close(); continue
                pt = read_point(page)
                results[idx] = (acc["id"], pt)
                print(f"[#{idx} {acc['id']}] {pt}", flush=True)
            except Exception as e:
                results[idx] = (acc["id"], f"ERR:{e}")
                print(f"[#{idx} {acc['id']}] ERR {e}", flush=True)
            finally:
                try: page.close()
                except Exception: pass
    print("\n===== H.Point 요약 =====", flush=True)
    for idx in idxs:
        cid, pt = results.get(idx, ("?", "?"))
        print(f"  #{idx:>2} {cid:<28} {pt}", flush=True)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "discover"
    if mode == "discover":
        discover(int(sys.argv[2]) if len(sys.argv) > 2 else 1)
    elif mode == "check":
        idxs = [int(x) for x in sys.argv[2:]] or list(range(1, len(_accounts()) + 1))
        check(idxs)
    else:
        print(__doc__)

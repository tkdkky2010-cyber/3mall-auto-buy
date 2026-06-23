"""현대몰 H.Point 이벤트 신청 + 구매내역(예상 H.Point 아코디언) 조회.

이벤트 페이지에서 'H.Point 신청하기' 클릭 → 확인 팝업 → '예상 H.Point' 아코디언 펼쳐
해당 이벤트 대상 제품을 얼마치 몇 개 샀는지 읽는다. 전 계정 반복.

사용:
    python3 buy/_hpoint_event_claim.py inspect 1     # #1 페이지 구조 덤프(신청 안 누름)
    python3 buy/_hpoint_event_claim.py run           # 전 계정 신청+조회
    python3 buy/_hpoint_event_claim.py run 1 2 5     # 지정 계정만

CDP 9222. login() = buy/run.py 재사용.
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
PRMO = "P202606041140"
EVENT_URL = f"https://www.hmall.com/md/eva/evntHPointDtl?prmoNo={PRMO}"


def _accounts() -> list[dict]:
    return json.loads(CFG.read_text(encoding="utf-8"))["accounts"]


def dismiss_pw_campaign(page) -> bool:
    """로그인 후 비밀번호 변경 캠페인(pwdChangePup) 인터스티셜 닫기 → '90일 후 변경'."""
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


def _login(page, acc) -> bool:
    logout_if_needed(page)
    ok = login(page, acc["id"], acc["pw"])
    if not ok and dismiss_pw_campaign(page):
        ok = True
    return ok


def inspect(idx: int) -> None:
    acc = _accounts()[idx - 1]
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(CDP_ENDPOINT)
        page = b.contexts[0].new_page()
        if not _login(page, acc):
            print(f"[#{idx} {acc['id']}] LOGIN_FAIL"); page.close(); return
        page.goto(EVENT_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        print(f"=== #{idx} {acc['id']} | {page.url} ===")
        info = page.evaluate(r"""() => {
          const out = {};
          const btn = document.querySelector('a.get-reward-btn');
          out.reward_btn = btn ? {text:(btn.innerText||'').trim(), cls:btn.className} : null;
          const acc = document.querySelector('button.accordion-trigger');
          out.accordion = acc ? {text:(acc.innerText||'').trim(), expanded:acc.getAttribute('aria-expanded'), controls:acc.getAttribute('aria-controls')} : null;
          return out;
        }""")
        print("[before expand]", json.dumps(info, ensure_ascii=False))
        # 아코디언 펼치기
        page.evaluate("() => { const a=document.querySelector('button.accordion-trigger'); if(a) a.click(); }")
        page.wait_for_timeout(2000)
        panel = page.evaluate(r"""() => {
          const acc = document.querySelector('button.accordion-trigger');
          if (!acc) return null;
          // 패널 후보: aria-controls 대상 → 다음 형제 → 부모의 다음 형제
          let panel = null;
          const cid = acc.getAttribute('aria-controls');
          if (cid) panel = document.getElementById(cid);
          if (!panel) panel = acc.nextElementSibling;
          if (!panel && acc.parentElement) panel = acc.parentElement.nextElementSibling;
          if (!panel) return {note:'panel not found', expanded:acc.getAttribute('aria-expanded')};
          return {tag:panel.tagName, cls:panel.className, html:panel.outerHTML.slice(0,1500), text:(panel.innerText||'').trim()};
        }""")
        print("[after expand]", json.dumps(panel, ensure_ascii=False, indent=2))
        page.close()


def _claim_if_needed(page) -> str:
    """신청버튼 상태 확인 → '신청하기'면 클릭+확인팝업, '완료'면 skip.
    반환: ALREADY / CLAIMED / CLICKED? / NO_BTN"""
    st = page.evaluate("""() => {
        const b = document.querySelector('a.get-reward-btn');
        if (!b) return {found:false};
        return {found:true, text:(b.innerText||'').trim(), complete:b.classList.contains('complete')};
    }""")
    if not st.get("found"):
        return "NO_BTN"
    if st.get("complete") or "완료" in st.get("text", ""):
        return "ALREADY"
    # 신청하기 클릭 (native confirm/alert 는 page dialog 핸들러가 accept)
    try:
        page.click("a.get-reward-btn", timeout=5000)
    except Exception:
        pass
    page.wait_for_timeout(1500)
    # 커스텀 모달 '확인' 버튼이 떠있으면 클릭
    page.evaluate("""() => {
        const els = Array.from(document.querySelectorAll('button, a'));
        const ok = els.find(b => /^확인$/.test((b.innerText||'').trim()) && b.offsetParent !== null);
        if (ok) ok.click();
    }""")
    page.wait_for_timeout(2000)
    after = page.evaluate("""() => { const b=document.querySelector('a.get-reward-btn');
        return b?{text:(b.innerText||'').trim(),complete:b.classList.contains('complete')}:null; }""")
    return "CLAIMED" if after and (after.get("complete") or "완료" in after.get("text", "")) else "CLICKED?"


def _expand_and_parse(page) -> dict:
    """예상 H.Point 아코디언 펼쳐 테이블 + 총합 파싱."""
    page.evaluate("""() => { const a=document.querySelector('button.accordion-trigger');
        if (a && a.getAttribute('aria-expanded')!=='true') a.click(); }""")
    page.wait_for_timeout(1500)
    return page.evaluate(r"""() => {
        const out = {expected_point:null, rows:[], total_amount:null, total_count:null};
        const accBtn = document.querySelector('button.accordion-trigger');
        if (accBtn) { const em = accBtn.querySelector('em'); out.expected_point = em ? em.innerText.trim() : null; }
        const panel = document.querySelector('.accordion-panel');
        if (panel) {
            for (const tr of panel.querySelectorAll('tbody tr')) {
                const td = tr.querySelectorAll('td');
                if (td.length>=3) out.rows.push({date:td[0].innerText.trim(), name:td[1].innerText.trim(), amount:td[2].innerText.trim()});
            }
            const add = panel.querySelector('.tbl-add');
            if (add) {
                const txt = (add.innerText||'').replace(/\s+/g,' ').trim();
                out.summary_text = txt;
                const m = txt.match(/총\s*([\d,]+)\s*원[\s\S]*?([\d,]+)\s*개/);
                if (m) { out.total_amount = m[1]; out.total_count = m[2]; }
            }
        }
        return out;
    }""")


def run(idxs: list[int]) -> None:
    accts = _accounts()
    results = {}
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(CDP_ENDPOINT)
        ctx = b.contexts[0]
        for idx in idxs:
            acc = accts[idx - 1]
            page = ctx.new_page()
            page.on("dialog", lambda d: d.accept())   # 확인 팝업 = native confirm → accept
            try:
                if not _login(page, acc):
                    results[idx] = {"id": acc["id"], "status": "LOGIN_FAIL"}
                    print(f"[#{idx} {acc['id']}] LOGIN_FAIL", flush=True)
                    page.close(); continue
                page.goto(EVENT_URL, wait_until="domcontentloaded")
                page.wait_for_timeout(2500)
                claim = _claim_if_needed(page)
                data = _expand_and_parse(page)
                data["id"] = acc["id"]; data["claim"] = claim
                results[idx] = data
                amt = data.get("total_amount"); cnt = data.get("total_count")
                print(f"[#{idx} {acc['id']}] {claim} | 예상H.Point={data.get('expected_point')}원 | "
                      f"총 {amt}원 & {cnt}개 | rows={len(data['rows'])}", flush=True)
                for r in data["rows"]:
                    print(f"      - {r['date']} {r['amount']:>12}  {r['name'][:50]}", flush=True)
            except Exception as e:
                results[idx] = {"id": acc["id"], "status": f"ERR:{e}"}
                print(f"[#{idx} {acc['id']}] ERR {e}", flush=True)
            finally:
                try: page.close()
                except Exception: pass

    out_path = ROOT / "logs" / f"hpoint_event_{PRMO}.json"
    out_path.write_text(json.dumps({"prmo": PRMO, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n===== 이벤트 H.Point 구매내역 요약 =====", flush=True)
    print(f"{'#':>3} {'ID':<22} {'신청':<9} {'예상P':>8} {'대상총액':>12} {'개수':>4}", flush=True)
    tot_amt = 0; tot_cnt = 0
    for idx in idxs:
        d = results.get(idx, {})
        if d.get("status"):
            print(f"{idx:>3} {d.get('id','?'):<22} {d['status']}", flush=True); continue
        amt = int((d.get("total_amount") or "0").replace(",", ""))
        cnt = int((d.get("total_count") or "0").replace(",", ""))
        tot_amt += amt; tot_cnt += cnt
        print(f"{idx:>3} {d.get('id','?'):<22} {d.get('claim',''):<9} "
              f"{d.get('expected_point','?'):>8} {amt:>12,} {cnt:>4}", flush=True)
    print(f"\n  합계: 대상총액 {tot_amt:,}원 / {tot_cnt}개", flush=True)
    print(f"  저장: {out_path}", flush=True)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "inspect"
    if mode == "inspect":
        inspect(int(sys.argv[2]) if len(sys.argv) > 2 else 1)
    elif mode == "run":
        idxs = [int(x) for x in sys.argv[2:]] or list(range(1, len(_accounts()) + 1))
        run(idxs)
    else:
        print(__doc__)

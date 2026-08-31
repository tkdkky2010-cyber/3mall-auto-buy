"""계정 결제 실패 **검수** — 실패한 순간의 증거를 자동으로 남긴다. (2026-08-31 신설)

사용자 지시 (문장 그대로, 2026-08-31):
  "윈도우결제는 지금 모든 카드 다 최초이니까 앞으로 윈도우에서 결제할 때는
   롯데몰, 현대몰 가릴거없이 모두 계정실패 왜 실패했는지 검수하는 과정 넣어놔"

왜 필요한가 (2026-08-31 하루치 실측):
  실패 상태명이 **원인을 가렸다.** `PAY_FAIL@hana_modal` 이 실제로는
    ㉠ 현금영수증 발급방식 미선택 팝업에 막힌 것이었고(#9·#10),
    ㉡ 다른 계정에선 화면 중간 '결제하기' 오탭이었다(#8).
  `LOGOUT_FAIL`(#9·#11)은 폰이 하나앱 결제화면에 갇힌 것이었다.
  그때마다 사람이 폰을 붙잡고 스크린샷을 떠서야 알 수 있었고, 그 사이 화면은
  다음 계정 로그인으로 넘어가 **증거가 사라졌다.** 실패 즉시 남겨야 한다.

남기는 것 (실패 계정 1건당):
  · status / mall / 계정 idx·id / 시각
  · **포그라운드 액티비티** (mCurrentFocus) — 폰이 어느 앱에 갇혔는지가 여기서 드러난다
  · 살아있는 카드앱 목록 — 잔재가 다음 계정을 무너뜨리는 연쇄의 증거
  · 화면 텍스트 (OCR+dump 병합, cy 순) — 팝업 문구가 여기 찍힌다
  · 스크린샷 PNG

출력: `phone_auto/_tmp/fail_audit/<날짜>/` 아래 PNG + `audit.jsonl` 누적.
      로그에도 요약 블록을 찍어 눈에 띄게 한다.

⚠️ 읽기 전용이다 — 화면을 탭하거나 앱을 죽이지 않는다(다음 재시도의 상태를 보존).
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUDIT_DIR = ROOT / "phone_auto" / "_tmp" / "fail_audit"

# 결제 도중 죽으면 앞에 떠서 다음 계정을 막는 카드앱들 (lotte CARD_APPS 와 같은 목록).
CARD_APPS = ("com.hanaskcard.paycla", "com.kbcard.kbkookmincard", "com.hanaskcard.rocomo.potal",
             "com.lcacApp", "com.shcard.smartpay", "kvp.jjy.MispAndroid320",
             "com.samsung.android.spay", "com.nh.cashcardapp", "nh.smart.card",
             "com.hyundaicard.appcard",          # ★현대카드 (2026-08-31 #14 실측 — 빠져 있었다)
             "com.kbstar.kbbank", "com.wooricard.smartapp", "com.citibank.cardapp",
             "com.hanaskcard.jayoung", "com.lotte.lottecard")

# 실패가 아닌 상태 = 검수 대상 아님. 핸드셰이크는 **정상 대기**지 실패가 아니다.
_OK_PREFIX = ("DONE", "STOP_BEFORE_PAY")


def is_failure(status: str | None) -> bool:
    """검수해야 할 실패인가. DONE/STOP_BEFORE_PAY/핸드셰이크 대기는 제외."""
    s = str(status or "")
    if not s or s.startswith(_OK_PREFIX):
        return False
    return "_HANDOFF" not in s


def _sh(args: list[str], timeout: int = 12) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout).stdout or ""
    except Exception as e:
        return f"(실행 실패: {e})"


def audit(idx, status, mall: str, serial: str | None = None, acc_id: str | None = None,
          texts_fn=None, cap_fn=None) -> dict:
    """실패 1건 검수. texts_fn/cap_fn 은 호출 모듈의 판독 헬퍼(_texts, cap)를 넘긴다.

    반환 dict 는 로그용 요약. 예외는 삼킨다 — **검수가 결제 흐름을 깨면 안 된다.**
    """
    out = {"idx": idx, "mall": mall, "id": acc_id, "status": str(status),
           "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    day = time.strftime("%Y-%m-%d")
    d = AUDIT_DIR / day
    adb = ["adb"] + (["-s", serial] if serial else [])
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    # 1) 포그라운드 액티비티 — '폰이 어디에 갇혔나'
    try:
        w = _sh(adb + ["shell", "dumpsys", "window"])
        out["focus"] = next((ln.strip() for ln in w.splitlines() if "mCurrentFocus" in ln),
                            "(mCurrentFocus 없음)")[:200]
    except Exception as e:
        out["focus"] = f"(판독 실패: {e})"

    # 2) 살아있는 카드앱 — 연쇄 실패의 원인
    try:
        ps = _sh(adb + ["shell", "ps", "-A"])
        out["card_apps_alive"] = [p for p in CARD_APPS if p in ps]
    except Exception as e:
        out["card_apps_alive"] = [f"(판독 실패: {e})"]

    # 3) 화면 텍스트 — 팝업 문구가 여기 찍힌다
    if texts_fn is not None:
        try:
            its = sorted(texts_fn(), key=lambda z: z["cy"])
            out["screen"] = [f"{it['cy']}:{it['text'][:34]}" for it in its[:28]]
        except Exception as e:
            out["screen"] = [f"(판독 실패: {e})"]

    # 4) 스크린샷
    if cap_fn is not None:
        try:
            png = d / f"{time.strftime('%H%M%S')}_{mall}_{idx}.png"
            cap_fn(str(png))
            out["shot"] = str(png)
        except Exception as e:
            out["shot"] = f"(캡처 실패: {e})"

    try:
        with (d / "audit.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(out, ensure_ascii=False) + "\n")
    except Exception:
        pass

    # 로그에 눈에 띄게
    print(f"\n{'─' * 54}\n[검수] #{idx} {mall} 실패 — {out['status'][:90]}", flush=True)
    print(f"   포그라운드: {out.get('focus', '')}", flush=True)
    if out.get("card_apps_alive"):
        # ⚠️ '살아있음' 자체가 원인은 아니다. com.samsung.android.spay 처럼 **상시 떠 있는
        #    시스템 서비스**도 잡힌다(2026-08-31 #13 오탐). 포그라운드가 카드앱일 때만 원인이다.
        _fg = out.get("focus", "")
        _stuck = [p for p in out["card_apps_alive"] if p in _fg]
        print(f"   살아있는 카드앱: {out['card_apps_alive']}"
              + (f"  ⚠️ 포그라운드가 카드앱 {_stuck} — 다음 계정 LOGOUT_FAIL 원인"
                 if _stuck else "  (상시 서비스일 수 있음 — 포그라운드가 롯데면 무관)"), flush=True)
    for ln in (out.get("screen") or [])[:14]:
        print(f"   화면 {ln}", flush=True)
    if out.get("shot"):
        print(f"   스샷: {out['shot']}", flush=True)
    print("─" * 54, flush=True)
    return out

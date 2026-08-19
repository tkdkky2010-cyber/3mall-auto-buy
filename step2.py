#!/usr/bin/env python3
"""Step 2 한 번에 — check10.py (Chrome 자동 launch 포함) + inspect. **맥/윈도우 공용.**

step2.sh 의 파이썬 이식본. 윈도우엔 bash 가 없어 .sh 를 못 쓰므로 이쪽이 정본이다.

사용:
    python3 step2.py        # 맥
    python step2.py         # 윈도우

★단일 CFT 원칙(2026-07-16): check10 도 로그인된 CFT 9222 를 재사용한다 —
  별도 Chrome 을 띄우지도, 죽이지도 않는다.
"""
from __future__ import annotations
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ★윈도우 cp949 → 한글 JSON 로드 실패 방지 (step1.py 와 동일 이유)
CHILD_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def run(script: Path, args: list[str] | None = None) -> int:
    return subprocess.run([sys.executable, str(script), *(args or [])],
                          cwd=ROOT, env=CHILD_ENV).returncode


def main() -> int:
    print(f"▶ Step 2 시작 (Hmall 10% 적립 체크) — "
          f"{datetime.now():%Y-%m-%d %H:%M:%S}  [{sys.platform}]\n")

    check_rc = run(ROOT / "cart" / "check10.py")

    print("\n═════════ 빠른 확인 (cart/show.py) ═════════", flush=True)
    run(ROOT / "cart" / "show.py")

    # 재고관리 시트 '당일 식품' 탭에 미러 (사용자 지시 2026-08-06 — 날짜탭 신설 없이 덮어쓰기).
    # ★2026-08-19: step2.sh 엔 있는데 **step2.py 엔 빠져 있었다** (step1.py 와 같은 누락).
    #   실패해도 step2 rc 는 check10 결과를 따른다(맥과 동일).
    print("\n═════════ 당일 식품 미러 ═════════", flush=True)
    if run(ROOT / "rate-check" / "mirror_daily.py", ["food"]) != 0:
        print("⚠️ 미러 실패 — 원본('M.DD 식품' 탭)은 정상. 수동: python rate-check/mirror_daily.py food")

    # check10 의 종료코드를 그대로 돌려준다 (show 실패는 판정에 안 넣음 — 조회 전용).
    return check_rc


if __name__ == "__main__":
    sys.exit(main())

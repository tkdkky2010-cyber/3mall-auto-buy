#!/usr/bin/env python3
"""Step 1 한 번에 — 3사 공급률 분석 + 재고관리 + 카트플랜(O열). **맥/윈도우 공용.**

step1.sh 의 파이썬 이식본. 윈도우엔 bash 가 없어 .sh 를 못 쓰므로 이쪽이 정본이다.
(step1.sh 는 맥 기존 사용자를 위해 남겨둠 — 내용 동일.)

사용:
    python3 step1.py        # 맥
    python step1.py         # 윈도우

★ inventory(사은품 재고버전) + cart_plan(O열) 누락 방지: 항상 자동 실행
  (2026-05-29 step1.sh 신설 이유).
★ 갤러리아 rc=2 = GWP 판독 대기(PENDING) — 안내 후 중단, 판독 JSON 작성하고 재실행하면 이어서 진행.
"""
from __future__ import annotations
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ★윈도우 기본 인코딩(cp949)이면 한글 JSON(sulwhasoo-ids.json 등) 로드가 UnicodeDecodeError 로 죽는다.
#   자식 프로세스에 UTF-8 을 강제해 encoding= 미지정 open() 들을 한 번에 안전하게 만든다.
CHILD_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

STEPS = [
    ("1/5 갤러리아",            ROOT / "rate-check" / "galleria.py",  []),
    ("2/5 재고관리 (inventory)", ROOT / "rate-check" / "inventory.py", ["--apply"]),
    ("3/5 Hmall",              ROOT / "rate-check" / "hmall.py",     []),
    ("4/5 롯데",               ROOT / "rate-check" / "lotte.py",     []),
    ("5/5 카트플랜 (O열)",       ROOT / "rate-check" / "cart_plan.py", []),
]


def run(script: Path, args: list[str]) -> int:
    return subprocess.run([sys.executable, str(script), *args], cwd=ROOT, env=CHILD_ENV).returncode


def main() -> int:
    print(f"▶ Step 1 시작 (3사 공급률 + 재고관리 + 카트플랜) — "
          f"{datetime.now():%Y-%m-%d %H:%M:%S}  [{sys.platform}]")
    for label, script, args in STEPS:
        print(f"\n═════════ {label} ═════════", flush=True)
        rc = run(script, args)
        # 갤러리아만 rc=2 = GWP 판독 PENDING (에러 아님) → 안내 후 정상 중단
        if script.name == "galleria.py" and rc == 2:
            print("\n⏸ GWP 판독 필요 — 위 안내대로 JSON 작성 후 재실행하세요:  python3 step1.py")
            return 2
        if rc != 0:
            print(f"\n❌ {label} 실패 (rc={rc})")
            return rc
    print(f"\n✅ Step 1 완료 — 3사 공급률 + 재고관리(n버전) + 카트플랜(O열) 모두 입력됨 — "
          f"{datetime.now():%H:%M:%S}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

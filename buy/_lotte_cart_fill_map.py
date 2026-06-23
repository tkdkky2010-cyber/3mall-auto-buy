"""롯데 cart-fill — 계정→조합 매핑 (비연속 계정 지원). cart-only.

사용:
    python3 buy/_lotte_cart_fill_map.py 23:6,11,12,13,16 3:17,20 [--delay 30]

각 spec = "<combo>:<idx,idx,...>". 계정마다 fresh subprocess(sulwhasoo.py lotte <idx> <combo>),
LOTTE_CART_ONLY=true. 계정 간 페이싱(기본 30s). _lotte_cart_fill.py 와 동일 메커니즘.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def main() -> int:
    args = [a for a in sys.argv[1:]]
    delay = 30
    if "--delay" in args:
        i = args.index("--delay"); delay = int(args[i + 1]); del args[i:i + 2]

    # (idx, combo) 순서 리스트로 펼치기 — 명시 순서 유지
    jobs: list[tuple[int, str]] = []
    for spec in args:
        combo, idxs = spec.split(":")
        for x in idxs.split(","):
            x = x.strip()
            if x:
                jobs.append((int(x), combo.strip()))

    print(f"▶ cart-fill 매핑 {len(jobs)}건: " + ", ".join(f"#{i}=조합{c}" for i, c in jobs), flush=True)
    env = {**os.environ, "LOTTE_CART_ONLY": "true"}
    results: list[tuple[int, str, bool]] = []
    for n, (idx, combo) in enumerate(jobs):
        print(f"\n===== 롯데 #{idx} 조합{combo} cart-fill ({n+1}/{len(jobs)}) =====", flush=True)
        r = subprocess.run([sys.executable, str(ROOT / "sulwhasoo.py"), "lotte", str(idx), combo], env=env)
        results.append((idx, combo, r.returncode == 0))
        if n < len(jobs) - 1:
            time.sleep(delay)

    print("\n========= cart-fill 요약 =========", flush=True)
    for idx, combo, ok in results:
        print(f"  #{idx:>2} 조합{combo}: {'✓ 담기완료' if ok else '✗ 실패'}", flush=True)
    return 0 if all(ok for _, _, ok in results) else 1

if __name__ == "__main__":
    sys.exit(main())

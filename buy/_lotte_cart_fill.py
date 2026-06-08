"""롯데 cart-fill 배치 — 계정 범위에 조합 N번 장바구니 담기 (cart-only).

캡차는 sulwhasoo.lotte_login 이 GCV 로 자동해결. bring_to_front 제거로 창 focus 안 뺏음.
계정마다 fresh subprocess(playwright 격리) + 페이싱(anti-bot 회피). 백그라운드 실행 권장:
    python3 buy/_lotte_cart_fill.py <combo> <start> <end> [delay_sec]
예) python3 buy/_lotte_cart_fill.py 14 1 7    # 조합14, 계정 1~7
"""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def main() -> int:
    combo = sys.argv[1] if len(sys.argv) > 1 else "14"
    start = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    end = int(sys.argv[3]) if len(sys.argv) > 3 else 7
    delay = int(sys.argv[4]) if len(sys.argv) > 4 else 30

    # 보이는 9222 Chrome 사용(headless 는 lotte 가 403 차단). focus 는 lotte_login 직접-URL(팝업X)로 회피.
    env = {**os.environ, "LOTTE_CART_ONLY": "true"}
    results = {}
    for idx in range(start, end + 1):
        print(f"\n===== 롯데 #{idx} 조합{combo} cart-fill =====", flush=True)
        r = subprocess.run([sys.executable, str(ROOT / "sulwhasoo.py"), "lotte", str(idx), combo], env=env)
        results[idx] = (r.returncode == 0)
        if idx < end:
            time.sleep(delay)  # 계정 간 페이싱

    print("\n========= cart-fill 요약 =========", flush=True)
    for idx, ok in results.items():
        print(f"  #{idx}: {'✓ 담기완료' if ok else '✗ 실패'}", flush=True)
    return 0 if all(results.values()) else 1

if __name__ == "__main__":
    sys.exit(main())

"""[스크래치·추측금지] '적용된 할인혜택이 초기화됩니다' 팝업의 **정확한 트리거 탭**을 잡는다.
장바구니부터 다시 출발 → 결제설정(주소/할인쿠폰/플러스쿠폰/포인트/현금/카드)을 코드 그대로 주행하되,
ADB.tap 을 패치해 매 탭마다: ①탭 직전 좌표 근처 OCR 라벨 기록 ②탭 후 팝업 텍스트 검사.
팝업이 뜨면 그 즉시 멈추고 [직전 탭 좌표 + 눌린 라벨 + 전/후 스크린샷] 출력.
usage: python3 phone_auto/_trace_popup.py 11
"""
from __future__ import annotations
import sys, time
sys.path.insert(0, ".")
sys.path.insert(0, "phone_auto")

import lotte_homeshopping_buy as L
from phone_auto.adb_input import ADB
from phone_auto.hmall_hyundai_buy import cap, _resolve_serial
from phone_auto.flow_runner import _ocr_texts

POPUP_KEYS = ("초기화", "적용된 할인혜택", "다시 적용")
SHOT = "phone_auto/_tmp_trigger"
_n = 0
_history = []          # (idx, x, y, nearest_label)


def _ocr():
    return _ocr_texts(cap())


def _nearest(items, x, y):
    if not items:
        return "?"
    it = min(items, key=lambda t: (t["cx"] - x) ** 2 + (t["cy"] - y) ** 2)
    d = ((it["cx"] - x) ** 2 + (it["cy"] - y) ** 2) ** 0.5
    return f"{it['text']!r}(d={int(d)})"


def _save(path):
    import subprocess
    subprocess.run(["adb", "exec-out", "screencap", "-p"], stdout=open(path, "wb"))


_orig_tap = ADB.tap


def traced_tap(self, x, y):
    global _n
    _n += 1
    idx = _n
    before = _ocr()
    label = _nearest(before, x, y)
    _history.append((idx, x, y, label))
    print(f"  TAP#{idx} ({x:4d},{y:4d})  ← {label}", flush=True)
    _orig_tap(self, x, y)
    time.sleep(0.9)
    after = _ocr()
    txt = " ".join(t["text"] for t in after)
    if any(k in txt for k in POPUP_KEYS):
        print(f"\n★★★ 팝업 트리거 발견 — TAP#{idx} ({x},{y}) ← {label}", flush=True)
        _save(f"{SHOT}_before.png")   # (after-tap 화면이지만 팝업 상태)
        print("   최근 5탭 이력:")
        for h in _history[-5:]:
            print(f"     #{h[0]} ({h[1]},{h[2]}) ← {h[3]}")
        print("   팝업 화면 OCR:")
        for t in sorted(after, key=lambda z: z["cy"]):
            print(f"     ({t['cx']:4d},{t['cy']:4d})  {t['text']}")
        raise SystemExit("STOP: 팝업 트리거 포착")


ADB.tap = traced_tap


def main():
    idx = int(sys.argv[1]) if len(sys.argv) > 1 else 11
    _resolve_serial()
    print(f"[#{idx}] 장바구니부터 재출발 — 팝업 트리거 추적", flush=True)
    L.reset_lotte_app(); L.dismiss_popups()
    if not L.logout():
        print("LOGOUT_FAIL"); return 1
    lr = L.login(idx)
    if not lr.get("ok"):
        print(f"LOGIN_FAIL:{lr.get('err')}"); return 1
    cs = L.goto_cart_select_all()
    print(f"  cart: {cs}", flush=True)
    if not cs.get("ok"):
        print(f"CART_FAIL:{cs.get('err')}"); return 1

    print("\n--- 결제설정 주행 (매 탭 추적) ---", flush=True)
    print(f"addr: {L.set_address()}", flush=True)
    print(f"discount: {L.set_discount_coupons()}", flush=True)
    print(f"plus: {L.set_plus_coupons()}", flush=True)
    print(f"points: {L.use_all_points()}", flush=True)
    print(f"cash: {L.set_cash_receipt()}", flush=True)
    print(f"card: {L.select_card_lotte(day=None)}", flush=True)
    print("\n▶ 팝업 없이 카드까지 완주 — 트리거 재현 안 됨.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

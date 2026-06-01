"""토스페이 탐색 도구 — 한 단계씩 수동 제어. ⚠️결제 X (PIN/최종결제 전에 사람이 멈춤).
  python3 -m phone_auto._toss nav <idx>    # 로그인→카트→전체선택→구매하기→주문서 도달 후 캡처+OCR
  python3 -m phone_auto._toss now          # 현재화면 캡처+OCR
  python3 -m phone_auto._toss tap <x> <y>  # 탭 후 캡처+OCR
  python3 -m phone_auto._toss swipe        # 아래로 스크롤(컨텐츠 위로) 후 캡처+OCR
  python3 -m phone_auto._toss swipeup      # 위로 스크롤 후 캡처+OCR
  python3 -m phone_auto._toss back         # 뒤로가기 후 캡처+OCR
"""
import os
import subprocess
import sys
import time

from phone_auto import hmall_hyundai_buy as m
from phone_auto import hmall_webview as hw

TMP = os.path.join("phone_auto", "_tmp")


def shot(serial, tag="now"):
    path = os.path.join(TMP, f"_toss_{tag}.png")
    with open(path, "wb") as f:
        subprocess.run([hw.ADB, "-s", serial, "exec-out", "screencap", "-p"], stdout=f)
    texts = m._ocr_texts(path)
    print(f"--- OCR ({len(texts)} items) ---", flush=True)
    for t in sorted(texts, key=lambda z: z["cy"]):
        print(f"  ({t['cx']:4d},{t['cy']:4d})  {t['text']}")
    print(f"캡처: {path}", flush=True)
    return path


def main() -> int:
    a = sys.argv[1:]
    m._resolve_serial()
    serial = hw._serial()
    if not a or a[0] == "now":
        shot(serial); return 0
    cmd = a[0]
    if cmd == "nav":
        idx = int(a[1])
        print(f"[nav] #{idx} → 주문서 (결제 X)", flush=True)
        hw.reset_to_main(serial)
        m.close_home_popup()
        lr = hw.login_account(idx, serial)
        if not lr.get("success"):
            print(f"LOGIN_FAIL:{lr.get('error')}"); return 1
        if hw.cart_state(serial).get("empty"):
            print("❌ 카트 비어있음"); return 1
        if not m.goto_cart():
            print("CART_NAV_FAIL"); return 1
        ok, sel = m.cdp_select_all()
        print(f"전체선택 {sel} ok={ok}", flush=True)
        if not m.ocr_tap("구매하기"):
            print("BUY_FAIL"); return 1
        if not m.wait_text("결제하기", timeout=15):
            print("ORDER_PAGE_FAIL"); return 1
        print("✅ 주문서 도달", flush=True)
        shot(serial, "order")
    elif cmd == "tap":
        x, y = int(a[1]), int(a[2])
        subprocess.run([hw.ADB, "-s", serial, "shell", "input", "tap", str(x), str(y)])
        time.sleep(1.3)
        print(f"[tap {x},{y}]", flush=True)
        shot(serial)
    elif cmd == "swipe":
        subprocess.run([hw.ADB, "-s", serial, "shell", "input", "swipe", "540", "1700", "540", "800", "400"])
        time.sleep(1.0)
        print("[swipe ↓ 컨텐츠 위로]", flush=True)
        shot(serial)
    elif cmd == "swipeup":
        subprocess.run([hw.ADB, "-s", serial, "shell", "input", "swipe", "540", "800", "540", "1700", "400"])
        time.sleep(1.0)
        print("[swipe ↑ 컨텐츠 아래로]", flush=True)
        shot(serial)
    elif cmd == "back":
        subprocess.run([hw.ADB, "-s", serial, "shell", "input", "keyevent", "4"])
        time.sleep(1.0)
        print("[back]", flush=True)
        shot(serial)
    elif cmd == "tapt":  # OCR 텍스트로 찾아 탭 (화면 적응)
        q = "".join(a[1:]).replace(" ", "")
        BLOCK = {"지금변경하기"}  # 실수로라도 누르면 안 되는 버튼
        if q in BLOCK:
            print(f"[tapt] 차단 버튼 '{q}' 거부", flush=True); return 3
        path = os.path.join(TMP, "_toss_now.png")
        with open(path, "wb") as f:
            subprocess.run([hw.ADB, "-s", serial, "exec-out", "screencap", "-p"], stdout=f)
        cand = [t for t in m._ocr_texts(path) if q in t["text"].replace(" ", "")]
        if not cand:
            print(f"[tapt] '{q}' 못 찾음", flush=True); shot(serial); return 2
        t = cand[0]
        subprocess.run([hw.ADB, "-s", serial, "shell", "input", "tap", str(t["cx"]), str(t["cy"])])
        time.sleep(1.3)
        print(f"[tapt '{q}'] @ ({t['cx']},{t['cy']})", flush=True)
        shot(serial)
    elif cmd == "type":  # adb input text (argv 전달 → 셸 미경유, 특수문자 안전)
        subprocess.run([hw.ADB, "-s", serial, "shell", "input", "text", a[1]])
        time.sleep(0.6)
        print(f"[type {len(a[1])}자]", flush=True)
    else:
        print("unknown cmd"); return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

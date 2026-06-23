"""#17 카드선택 오진 라이브 재현 — 주문서까지 가서 select_card_discount 각 단계 캡처.
결제하기 탭 안 함 (무과금). 사용: /usr/bin/python3 -m phone_auto._debug_card_select_17"""
from __future__ import annotations
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from phone_auto import hmall_webview as hw
from phone_auto.flow_runner import _ocr_texts
from phone_auto.hmall_hyundai_buy import (
    _resolve_serial, _adb, cap, close_home_popup, goto_cart, cdp_select_all,
    ocr_tap, wait_text, detect_card)

IDX = 17


def dump(tag: str):
    png = f"/tmp/_dbg17_{tag}.png"
    import subprocess
    subprocess.run(["adb", "exec-out", "screencap", "-p"], stdout=open(png, "wb"))
    its = _ocr_texts(png)
    print(f"\n--- [{tag}] OCR items ---")
    for it in its:
        print(f"  ({it['cx']:4d},{it['cy']:4d}) w{it['w']:4d} {it['text']!r}")
    return png, its


def main():
    _resolve_serial()
    serial = hw._serial()
    hw.reset_to_main(serial)
    close_home_popup()
    lr = hw.login_account(IDX, serial)
    print("login:", lr.get("success"), lr.get("id"))
    if not lr.get("success"):
        return 1
    if not goto_cart():
        print("cart nav fail"); return 1
    ok, sel = cdp_select_all()
    print("select_all:", ok, sel)
    if not ok:
        return 1
    if not ocr_tap("구매하기"):
        print("구매하기 실패"); return 1
    if not wait_text("결제하기", timeout=15):
        print("주문서 미도달"); return 1
    time.sleep(1.5)
    dump("00_order_page")

    day = detect_card()
    print("\nday_card =", day)

    # ── select_card_discount 수동 재현 (탭마다 캡처) ──
    for i in range(6):
        png, its = dump(f"{10+i}_scroll{i}")
        hdr = next((it for it in its if it["text"].strip() == "카드할인"), None)
        if hdr:
            pmt = next((it for it in its if "결제수단" in it["text"] and it["cy"] > hdr["cy"]), None)
            y_lo, y_hi = hdr["cy"], (pmt["cy"] if pmt else hdr["cy"] + 700)
            rows = [it for it in its if re.search(r"[\d,]{4,}\s*원", it["text"])
                    and y_lo < it["cy"] < y_hi and "결제하기" not in it["text"]]
            rows.sort(key=lambda it: it["cy"])
            print("카드할인 hdr:", (hdr["cx"], hdr["cy"]), "| pmt:", pmt and (pmt["text"], pmt["cy"]),
                  "| rows:", [(r["text"], r["cy"]) for r in rows])
            if rows:
                _adb().tap(350, rows[0]["cy"]); time.sleep(1.8)
                png2, its2 = dump("20_after_row_tap")
                t2 = " ".join(x["text"] for x in its2)
                print("\n'신용카드 선택' in t2:", "신용카드 선택" in t2)
                print("'카드종류' in t2:", "카드종류" in t2)
                print("'현대카드' in t2:", "현대카드" in t2)
                # 결제수단 섹션이 화면 밖일 수 있음 — 스크롤 내려 결제수단 보이게
                for j in range(3):
                    its3 = dump(f"3{j}_pmt_scroll{j}")[1]
                    pm = next((it for it in its3 if "결제수단" in it["text"]), None)
                    if pm:
                        near = [it["text"] for it in its3 if pm["cy"] - 40 < it["cy"] < pm["cy"] + 400]
                        print(f"결제수단 행 발견 (cy={pm['cy']}): 주변 텍스트 = {near}")
                        break
                    _adb().swipe(540, 1700, 540, 900, 400); time.sleep(0.8)
                print("\n[STOP] 결제 진행 안 함 — 캡처/덤프만.")
                return 0
        _adb().swipe(540, 1700, 540, 800, 400); time.sleep(0.8)
    print("카드할인 섹션 못 찾음")
    return 1


if __name__ == "__main__":
    sys.exit(main())

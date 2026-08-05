#!/usr/bin/env python3
"""NH 일반결제 비전 핸드세이크 러너 — 에이전트가 판독한 키패드 배열로 실제 입력을 수행한다.

`pay_nh_general` 이 카드번호 화면에서 `manual=True` 로 정지한 뒤 이어받는 도구.
**값(카드번호/CVC/비번)은 이 스크립트가 secrets/card_secrets.json['NH'] 에서 직접 읽는다.**
에이전트는 값을 보지 않고 **키패드 배열만** 넘긴다 (판독=에이전트 / 탭=코드).

배열 규칙 (nh_vision_input 실측, 2026-07-31 라이브):
  · 6열 2행. 방패 아이콘 자리는 `shield` 로 적는다. 빈 칸은 `-`.
  · **한 칸(4자리) 입력 중에는 배열 고정** → 4자리 연속 탭 안전.
  · **칸이 바뀌면 재셔플** → 칸마다 반드시 새 스크린샷을 다시 판독할 것.

사용:
  python3 -m phone_auto.nh_enter shot /tmp/kp.png          # 전체화면 캡처(판독용)
  python3 -m phone_auto.nh_enter box1                      # 1칸 = 비보안 IME (배열 불필요)
  python3 -m phone_auto.nh_enter box2 "9,0,4,6,shield,1" "8,7,2,shield,3,5"
  python3 -m phone_auto.nh_enter cvc  "..." "..."
  python3 -m phone_auto.nh_enter pinfield                  # 결제비번 칸 탭(키패드 소환)
  python3 -m phone_auto.nh_enter pin6 "..." "..."
  python3 -m phone_auto.nh_enter confirm                   # '확인' 탭
  python3 -m phone_auto.nh_enter finish 5 데이즈온          # ★주문완료 화면에서 대장+적립 마무리

★★`finish` 를 빼먹지 말 것. NH 는 buy_one 이 핸드세이크 지점에서 일찍 return 하므로
   구매대장·H.Point 적립 자동단계를 **안 탄다**. 2026-08-05 에 이걸 몰라 적립 12계정이
   조용히 누락됐다(에러도 안 남). 전체 순서:
     box1 → box2 → box3 → box4 → cvc → confirm → pinfield → pin6 → confirm → **finish**

⚠️ 자릿수는 화면에서 검증한다(card_digits_on_screen). 틀린 키를 누르면 카드사 입력오류가
   누적돼 3회에 카드가 잠길 수 있으므로, 매핑에 없는 숫자가 있으면 탭하지 않고 중단한다.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time

from phone_auto import hmall_hyundai_buy as B
from phone_auto.nh_vision_input import (KEY_COLS, ROW_CARD, ROW_CVC, ROW_PIN6,
                                        build_pos, screencap, tap_digits)

_ROWS = {"card": ROW_CARD, "cvc": ROW_CVC, "pin6": ROW_PIN6}


def _secrets() -> dict:
    sec = B._card_secrets().get("NH", {})
    if not all(sec.get(k) for k in ("card_no", "cvc", "pin6")):
        raise SystemExit("card_secrets['NH'] 필드 부족(card_no/cvc/pin6)")
    return sec


def _parse(spec: str) -> list[str]:
    out = [s.strip() for s in spec.split(",")]
    if len(out) != len(KEY_COLS):
        raise SystemExit(f"열 개수 {len(out)} (기대 {len(KEY_COLS)}) — 6열로 적을 것: {spec!r}")
    return [("" if s in ("-", "") else s) for s in out]


def _keypad_up() -> bool:
    """보안키패드가 이미 떠 있는가 ('가상키패드' 헤더로 판정)."""
    return B.screen_has("가상키패드")


def _nh_field_len(substr: str = "cardno") -> dict[int, int] | None:
    """NH 카드번호 4칸 각각에 **실제로 들어간 글자 수** {1:4, 2:4, ...}. 못 읽으면 None(=검증 불가).

    ★왜 필요한가: 공용 `card_digits_on_screen()` 은 `1234-****-****-5678` 같은 **한 덩어리 마스킹
      필드**를 찾는다(`[\\d*\\-]{8,}` + '-'/'*' 포함). NH 는 **4칸이 따로 떨어져** 있고 채워진 표시가
      `•` 라 정규식이 절대 안 맞아 **항상 0** 을 반환한다 → 오늘(8/5) 매번 '자릿수 0' 이 찍혔다.
      즉 2026-08-02 에 넣은 '탭 씹힘' 가드가 NH 에선 무방비였다.
      7/31 실측: ADB `input tap` 이 NH 보안키패드에서 **간헐적으로 씹힌다**(4탭→2개 인식).
      틀린 자릿수로 진행하면 카드사 입력오류가 쌓이고 3회면 카드가 잠긴다 → 반드시 칸별로 검증한다.

    dump 의 EditText `text` 를 우선 보고, 비어 보이면 `content-desc` 로 폴백한다."""
    import re as _re
    import xml.etree.ElementTree as ET
    p = "/tmp/_nh_fieldlen.xml"
    try:
        B._adb().dump_ui(p)
        root = ET.parse(p).getroot()
    except Exception:
        return None
    out: dict[int, int] = {}
    for n in root.iter():
        if "EditText" not in n.attrib.get("class", ""):
            continue
        m = _re.search(rf"{substr}(\d)", n.attrib.get("resource-id", ""))
        if not m:
            continue
        val = (n.attrib.get("text") or "").strip() or (n.attrib.get("content-desc") or "").strip()
        out[int(m.group(1))] = len(val)
    return out or None


def _verify_box(n: int, expect: int = 4) -> bool:
    """n번째 칸이 expect 자리로 채워졌는지. 검증 불가면 True(진행)하되 **경고를 남긴다** —
    조용히 넘어가면 8/5 처럼 '검증한 줄 알았는데 안 한' 상태가 된다."""
    st = _nh_field_len()
    if st is None or n not in st:
        print(f"  [verify] ⚠️ {n}칸 자릿수 판독 불가 — 검증 없이 진행(화면 직접 확인 권장)")
        return True
    got = st[n]
    if got == expect:
        print(f"  [verify] ✓ {n}칸 {got}자리")
        return True
    print(f"  [verify] ✗ {n}칸 {got}자리 (기대 {expect}) — **탭 씹힘 의심. 중단한다.**\n"
          f"           '모두지움' 후 그 칸부터 다시 판독·입력할 것 "
          f"(틀린 채 진행하면 카드사 입력오류 누적 → 3회에 카드 잠김)")
    return False


def _tap_box(n: int, force: bool = False) -> None:
    """카드번호 n번째 칸 탭. box1 입력 후 레이아웃이 아래로 이동하므로 매번 fresh 재검출.

    ★★키패드가 **이미 떠 있으면 탭하지 않는다.** 재탭은 키패드를 리셋(재셔플)시켜
      방금 판독한 배열이 무효가 되고 → 엉뚱한 키가 눌린다 → 카드사 입력오류 누적
      3회면 카드 잠김. (한 칸 입력이 끝나면 다음 칸이 자동 활성 + 키패드가 새로 뜬다.)"""
    if _keypad_up() and not force:
        print(f"  [box{n}] 키패드 이미 표시중 → 재탭 skip (재셔플 방지)")
        return
    boxes = B._card_no_boxes()
    if len(boxes) < n:
        raise SystemExit(f"카드번호 입력칸 {len(boxes)}개 — {n}칸 미검출 (화면 확인 필요)")
    x, y = boxes[n - 1]
    B._adb().tap(x, y)
    time.sleep(1.2)


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 0
    cmd = args[0]

    if cmd == "shot":
        path = args[1] if len(args) > 1 else "/tmp/nh_keypad.png"
        B._resolve_serial()
        print(screencap(path))
        return 0

    B._resolve_serial()
    sec = _secrets()

    if cmd == "box1":
        # 1칸 = **비보안 IME** 칸 → adb input text. 이 입력 완료가 2칸 nppfs 키패드를 소환한다
        # (2026-06-25 실측: 키 탭으로 1칸을 채우면 다음 칸 키패드가 안 뜬다).
        _tap_box(1, force=True)
        subprocess.run(["adb", "shell", "input", "text", sec["card_no"][:4]],
                       capture_output=True)
        time.sleep(1.5)
        print("[box1] IME 입력 완료")
        return 0 if _verify_box(1) else 1

    if cmd == "confirm":
        ok = B.ocr_tap("확인", contains=True, retries=4)
        print(f"[confirm] {'✓' if ok else '✗'}")
        return 0 if ok else 1

    if cmd == "finish":
        # ★NH 마무리 — 주문번호 판독 → 구매대장 → **H.Point 적립신청**.
        #   NH 는 buy_one 이 핸드세이크 지점에서 일찍 return 하므로 대장/적립 자동단계를 **안 탄다.**
        #   그래서 여기서 반드시 마무리해야 한다. (2026-08-05: 이걸 안 해서 적립 12계정 누락)
        if len(args) < 2 or not args[1].isdigit():
            print("[ERR] 사용: python3 -m phone_auto.nh_enter finish <계정번호> [상품키워드...]")
            return 1
        idx, kws = int(args[1]), args[2:]
        import re as _re
        its = B._ocr_texts(B.cap())
        txt = " ".join(i["text"] for i in its)
        m = _re.search(r"주문번호\s*[:：]?\s*(\d{8,})", txt) or _re.search(r"\b(2026\d{9,})\b", txt)
        order_no = m.group(1) if m else None
        # ★주문완료 화면인지 **먼저** 확인 — 결제가 안 됐는데 대장·적립을 기록하면
        #   있지도 않은 구매가 장부에 남는다(더 나쁜 오류). 확인 안 되면 아무것도 하지 않는다.
        completed = bool(order_no) or ("주문이" in txt and "완료" in txt) or "주문 완료" in txt
        if not completed:
            print("[finish] ✗ 주문완료 화면이 아니다 (주문번호·완료문구 없음) — "
                  "대장/적립 **기록하지 않고 중단**.\n"
                  "         결제가 실제로 끝났는지 화면을 확인하고, 끝났으면 그 화면에서 다시 실행할 것.")
            return 1
        print(f"[finish] 주문완료 확인 — 주문번호 {order_no or '(미판독)'}")

        accounts = json.loads(B.hw.ACCOUNTS_FILE.read_text(encoding="utf-8"))["accounts"]
        acct_id = accounts[idx - 1].get("id")
        # 구매대장 — 이번에 결제한 상품만(키워드 필터)
        try:
            mf = json.loads((B.ROOT / "cart" / "today_carts.json").read_text(encoding="utf-8"))
            cart = next((c for c in mf.get("carts", [])
                         if c.get("mall") in ("현대", "hmall") and c.get("account") == idx), None)
            sys.path.insert(0, str(B.ROOT))
            import purchase_ledger as PL
            n = 0
            for it in (cart or {}).get("items", []):
                if kws and not any(k in (it.get("name") or "") for k in kws):
                    continue
                PL.record_food("현대Hmall", acct_id, it.get("product"), qty=it.get("qty"),
                               order_no=order_no, card="NH")
                n += 1
            print(f"[finish] 구매대장 {n}건 기록")
        except Exception as e:
            print(f"[finish] ⚠️ 대장 기록 실패: {e}")
        B.apply_reward_now(idx, kws or None)      # ★적립 (코드가 한다)
        return 0

    if cmd == "pinfield":
        # 카드 확인 후 '일반결제비밀번호(숫자 6자리)' 칸 — **탭해야 보안키패드가 뜬다.**
        pf = next((it for it in B._ocr_texts(B.cap())
                   if "6자리" in it["text"] or "숫자" in it["text"]), None)
        if not pf:
            print("[pinfield] ✗ '숫자 6자리' 칸 미발견")
            return 1
        B._adb().tap(pf["cx"], pf["cy"])
        time.sleep(1.5)
        print(f"[pinfield] ✓ 탭 @({pf['cx']},{pf['cy']}) — 키패드 {'O' if _keypad_up() else 'X'}")
        return 0

    if cmd not in ("box2", "box3", "box4", "cvc", "pin6"):
        print(f"unknown cmd: {cmd}")
        return 1
    if len(args) < 3:
        print(f"배열 2행이 필요합니다: {cmd} \"<1행 6열>\" \"<2행 6열>\"")
        return 1

    row1, row2 = _parse(args[1]), _parse(args[2])
    if cmd.startswith("box"):
        n = int(cmd[-1])
        value = sec["card_no"][(n - 1) * 4: n * 4]
        _tap_box(n)
        B._wait_keypad(timeout=6); time.sleep(0.8)
        pos = build_pos(row1, row2, _ROWS["card"])
    elif cmd == "cvc":
        value = sec["cvc"]
        pos = build_pos(row1, row2, _ROWS["cvc"])
    else:
        value = sec["pin6"]
        pos = build_pos(row1, row2, _ROWS["pin6"])

    r = tap_digits(value, pos)
    # ⚠️ 값 자체는 절대 출력하지 않는다 (자릿수만).
    print(f"[{cmd}] {'✓' if r.get('ok') else '✗'} {r.get('digits', 0)}자리"
          f"{'' if r.get('ok') else ' — ' + str(r.get('err'))}")
    if not r.get("ok"):
        return 1
    # ★탭 씹힘 검증 — 여기서 안 잡으면 틀린 번호로 '확인'까지 가서 카드사 입력오류가 쌓인다.
    if cmd.startswith("box"):
        time.sleep(0.6)
        return 0 if _verify_box(int(cmd[-1])) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

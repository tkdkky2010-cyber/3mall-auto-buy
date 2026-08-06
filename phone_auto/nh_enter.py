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
  python3 -m phone_auto.nh_enter finish 5 데이즈온          # ★현대몰 식품: 대장(record_food)+H.Point 적립
  python3 -m phone_auto.nh_enter finish 5 combo=24         # ★현대몰 설화수: 대장(record_combo)
  python3 -m phone_auto.nh_enter finish_lotte 5 combo=24    # ★롯데: 대장+뷰티포인트+구매사은 적립
  python3 -m phone_auto.nh_enter fields                    # (디버그) 입력칸 resource-id/자릿수 덤프

★공통 옵션 (2026-08-07 추가 — 안 넣으면 대장이 조용히 틀리게 적힌다):
  · `card=삼성`  … 기본값은 NH. **삼성으로 결제했으면 반드시 넘긴다**(samsung_enter 가 자동 주입).
  · `combo=24`  … 설화수(조합 단위). 없으면 식품으로 보고 today_carts.json 을 뒤진다.
  · `order=2026…` … 주문완료 화면을 이미 벗어났을 때만. 화면 검증을 건너뛰므로 로그에 크게 남는다.

★★`finish` 를 빼먹지 말 것. NH 는 buy_one 이 핸드세이크 지점에서 일찍 return 하므로
   구매대장·적립 자동단계를 **안 탄다**. 2026-08-05 에 이걸 몰라 H.Point 적립 12계정이
   조용히 누락됐다(에러도 안 남). 전체 순서:
     box1 → box2 → box3 → box4 → cvc → confirm → pinfield → pin6 → confirm → **finish(_lotte)**

★몰마다 마무리가 다르다 — 섞어 쓰면 딴 몰 장부에 기록된다(foreground 앱으로 가드함):
   · `finish`       = 현대몰 → record_food + **H.Point 적립**(건강식품 10% 등)
   · `finish_lotte` = 롯데   → record_combo + **뷰티포인트** + **구매사은 적립금**
     ⚠️뷰티 → 적립 순서 고정. reward 가 홈으로 이동하면 주문완료 화면을 이탈해 뷰티가 소실된다.

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

# CVC·결제비번 칸의 resource-id 는 **라이브 실측 전**이다(카드번호 cardno1~4 만 확정).
# 후보를 훑어 하나도 못 맞으면 '검증 불가' 로 경고만 남긴다 — `fields` 로 실제 id 를 보고 여기에 추가할 것.
_CVC_KEYS = ("cvc", "cvv")
_PIN_KEYS = ("pass", "pwd", "pin")


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
    fs = _edit_fields()
    if fs is None:
        return None
    out: dict[int, int] = {}
    for rid, ln in fs:
        m = _re.search(rf"{substr}(\d)", rid)
        if m:
            out[int(m.group(1))] = ln
    return out or None


def _edit_fields() -> list[tuple[str, int]] | None:
    """화면의 모든 EditText → [(resource-id, 입력된 글자수)]. dump 실패면 None(=검증 불가).
    `text` 우선, 비어 보이면 `content-desc` 폴백(마스킹 `•` 도 글자수로 잡힌다)."""
    import xml.etree.ElementTree as ET
    p = "/tmp/_nh_fieldlen.xml"
    try:
        B._adb().dump_ui(p)
        root = ET.parse(p).getroot()
    except Exception:
        return None
    out = []
    for n in root.iter():
        if "EditText" not in n.attrib.get("class", ""):
            continue
        val = (n.attrib.get("text") or "").strip() or (n.attrib.get("content-desc") or "").strip()
        out.append((n.attrib.get("resource-id", ""), len(val)))
    return out or None


def _verify_len(keys: tuple, expect: int, label: str) -> bool:
    """인덱스 없는 칸(CVC·결제비번) 자릿수 검증. box1~4 와 달리 **0자리는 실패로 보지 않는다** —
    id 를 라이브로 확정하지 못해 '마스킹돼 안 읽히는 것'과 '탭이 다 씹힌 것'을 구분할 수 없기 때문.
    0 < got != expect 만 확실한 씹힘이므로 그때만 중단한다(오탐 중단이 더 위험 — 결제 중간에 멈춘다)."""
    fs = _edit_fields()
    if fs is None:
        print(f"  [verify] ⚠️ {label} 판독 불가(dump 실패) — 검증 없이 진행")
        return True
    hits = [ln for rid, ln in fs if any(k in rid.lower() for k in keys)]
    if not hits:
        print(f"  [verify] ⚠️ {label} 입력칸 id 미발견 — 검증 없이 진행 "
              f"(`nh_enter fields` 로 실제 id 확인 후 nh_enter.py 의 후보에 추가할 것)")
        return True
    got = max(hits)
    if got == expect:
        print(f"  [verify] ✓ {label} {got}자리")
        return True
    if got == 0:
        print(f"  [verify] ⚠️ {label} 0자리로 읽힘 — 마스킹인지 탭 씹힘인지 불명 → 진행하되 화면 직접 확인")
        return True
    print(f"  [verify] ✗ {label} {got}자리 (기대 {expect}) — **탭 씹힘. 중단한다.**\n"
          f"           지우고 다시 판독·입력할 것 (틀린 채 확인하면 카드사 입력오류 누적 → 3회에 카드 잠김)")
    return False


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


def _finish_lotte(args: list[str]) -> int:
    """롯데 NH 마무리 — 주문완료 확인 → 구매대장 → **뷰티포인트** → **구매사은 적립금**.

    롯데도 NH 는 `buy_one` 이 `NH_HANDOFF` 로 일찍 return 하므로(lotte_homeshopping_buy.py:1369)
    DONE 이후 후처리를 통째로 안 탄다 → 여기서 같은 순서로 마무리한다.
    ⚠️순서 고정(뷰티 → 적립): reward 가 홈으로 이동하면 주문완료 화면을 이탈해 뷰티가 소실된다(#6 사례).
    사용: finish_lotte <계정번호> [combo=24] [goods=2923406968] [card=삼성]"""
    from phone_auto import lotte_homeshopping_buy as L
    if not args or not args[0].isdigit():
        print("[ERR] 사용: python3 -m phone_auto.nh_enter finish_lotte <계정번호> "
              "[combo=N] [goods=상품번호] [card=삼성]")
        return 1
    idx = int(args[0])
    combo = next((int(x.split("=", 1)[1]) for x in args if x.startswith("combo=")), None)
    goods = next((x.split("=", 1)[1] for x in args if x.startswith("goods=")), None)
    # ★card= : 종전 card="NH" 하드코딩 → 삼성으로 결제해도 대장엔 NH 로 적혔다(8/7 발견).
    card = next((x.split("=", 1)[1] for x in args if x.startswith("card=")), "NH")
    if not B._wait_app(L.PKG, timeout=2):
        print("[finish] ✗ 롯데앱이 foreground 가 아니다 — 주문완료 화면에서 실행할 것 (기록 안 함)")
        return 1
    # ★주문완료를 **먼저** 확인 — 결제가 안 됐는데 기록하면 있지도 않은 구매가 장부에 남는다(§17②와 동일 규칙).
    confirmed, order = L._poll_order_complete(20)
    if not confirmed:
        print("[finish] ✗ 주문완료 화면이 아니다 (완료문구·주문번호 없음) — 대장/적립 **기록하지 않고 중단**")
        return 1
    print(f"[finish] 주문완료 확인 — 주문번호 {order or '(미판독)'}")
    acct_id = L._accounts()[idx - 1].get("id")
    try:
        sys.path.insert(0, str(B.ROOT))
        import purchase_ledger as PL
        PL.record_combo("롯데홈쇼핑", acct_id, combo, order_no=order, card=card)
    except Exception as e:
        print(f"[finish] ⚠️ 대장 기록 실패: {e}")
    L.dismiss_card_register()
    bp = L.claim_beauty_point(idx)
    print(f"[finish] 뷰티포인트 {'✓' if bp.get('completed') else '⚠️ ' + str(bp.get('skip') or bp.get('err'))}")
    rw = L.claim_lotte_reward(goods_no=goods)
    print(f"[finish] 구매사은 적립 {'✓' if rw.get('completed') or rw.get('already') else '⚠️ ' + str(rw.get('skip') or rw.get('err'))}")
    return 0


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

    if cmd == "fields":
        # 디버그 — CVC/결제비번 칸의 실제 resource-id 확인용. 값은 안 찍고 자릿수만.
        for rid, ln in (_edit_fields() or []):
            print(f"  {rid or '(id없음)'}  → {ln}자리")
        return 0

    if cmd == "confirm":
        # ★'확인' 완전일치를 먼저 본다. contains 로 잡으면 '확인해주세요' 같은 안내문구를 눌러
        #   결제가 엉뚱한 데로 샌다. 완전일치 0건일 때만(OCR 이 '확 인' 으로 띄어 읽는 경우) contains 폴백.
        cands = [it for it in B._ocr_texts(B.cap()) if it["text"].strip() == "확인"]
        if len(cands) > 1:
            print(f"[confirm] '확인' {len(cands)}개 — 맨 아래 것 선택")
        ok = B.ocr_tap("확인", contains=False, pick="bottom", retries=3)
        if not ok:
            print("[confirm] ⚠️ '확인' 완전일치 없음 → contains 폴백(오탭 주의)")
            ok = B.ocr_tap("확인", contains=True, pick="bottom", retries=2)
        print(f"[confirm] {'✓' if ok else '✗'}")
        return 0 if ok else 1

    if cmd == "finish":
        # ★NH 마무리 — 주문번호 판독 → 구매대장 → **H.Point 적립신청**.
        #   NH 는 buy_one 이 핸드세이크 지점에서 일찍 return 하므로 대장/적립 자동단계를 **안 탄다.**
        #   그래서 여기서 반드시 마무리해야 한다. (2026-08-05: 이걸 안 해서 적립 12계정 누락)
        if len(args) < 2 or not args[1].isdigit():
            print("[ERR] 사용: python3 -m phone_auto.nh_enter finish <계정번호> [상품키워드...] "
                  "[card=삼성] [combo=24] [order=2026...]")
            return 1
        idx = int(args[1])
        opt = args[2:]
        # ★card= : 종전엔 card="NH" 하드코딩이라 **삼성으로 결제해도 대장엔 NH** 로 적혔다(8/7 발견).
        #   samsung_enter 가 위임할 때 card=삼성 을 자동으로 넣는다.
        card = next((a.split("=", 1)[1] for a in opt if a.startswith("card=")), "NH")
        # ★combo= : 현대몰 **설화수**는 식품(record_food)이 아니라 조합(record_combo)으로 적는다.
        #   종전엔 현대몰 finish 에 이 분기가 없어 설화수를 삼성/NH 로 사면 대장이 틀어졌다.
        combo = next((int(a.split("=", 1)[1]) for a in opt if a.startswith("combo=")), None)
        # ★order= : 주문완료 화면을 이미 벗어난 뒤(알림 탭/자동 이동) 기록을 살리는 수동 인계용.
        order_given = next((a.split("=", 1)[1] for a in opt if a.startswith("order=")), None)
        kws = [a for a in opt if "=" not in a]
        # ★몰 가드 — 롯데 주문완료 화면에서 이걸 돌리면 현대몰 카트를 찾아 **딴 몰 장부**에 기록하고
        #   H.Point 적립을 돌린다(뷰티포인트는 그동안 소실). 롯데면 finish_lotte 로 보낸다.
        from phone_auto import lotte_homeshopping_buy as _L
        if B._wait_app(_L.PKG, timeout=1):
            print("[finish] ✗ 롯데앱이 foreground 다 — 롯데는 `finish_lotte` 를 쓸 것 (기록 안 함)")
            return 1
        import re as _re
        its = B._ocr_texts(B.cap())
        txt = " ".join(i["text"] for i in its)
        m = _re.search(r"주문번호\s*[:：]?\s*(\d{8,})", txt) or _re.search(r"\b(2026\d{9,})\b", txt)
        order_no = m.group(1) if m else None
        # ★주문완료 화면인지 **먼저** 확인 — 결제가 안 됐는데 대장·적립을 기록하면
        #   있지도 않은 구매가 장부에 남는다(더 나쁜 오류). 확인 안 되면 아무것도 하지 않는다.
        completed = bool(order_no) or ("주문이" in txt and "완료" in txt) or "주문 완료" in txt
        if order_given:
            # ★수동 인계 — 주문완료 화면을 이미 벗어난 경우(알림 배너 탭 등으로 상품페이지로 이동).
            #   결제는 끝났는데 화면이 넘어갔다는 이유로 대장·적립을 통째로 못 남기면
            #   2026-08-05 의 '조용한 누락' 과 결과가 같아진다 → 근거(주문번호)를 받아 기록한다.
            #   ⚠️화면 검증을 건너뛰는 유일한 경로이므로 **크게 로그로 남긴다.**
            order_no, completed = order_given, True
            print(f"[finish] ⚠️ order= 로 주문번호를 직접 받았다({order_given}) — 화면 확인 없이 기록한다. "
                  "실제 주문내역과 반드시 대조할 것")
        if not completed:
            print("[finish] ✗ 주문완료 화면이 아니다 (주문번호·완료문구 없음) — "
                  "대장/적립 **기록하지 않고 중단**.\n"
                  "         결제가 실제로 끝났는지 화면을 확인하고, 끝났으면 그 화면에서 다시 실행할 것.\n"
                  "         (화면이 이미 넘어갔으면 `order=<주문번호>` 로 기록만 살릴 수 있다)")
            return 1
        print(f"[finish] 주문완료 확인 — 주문번호 {order_no or '(미판독)'} / 카드 {card}")

        accounts = json.loads(B.hw.ACCOUNTS_FILE.read_text(encoding="utf-8"))["accounts"]
        acct_id = accounts[idx - 1].get("id")
        # 구매대장 — 이번에 결제한 상품만(키워드 필터)
        try:
            sys.path.insert(0, str(B.ROOT))
            import purchase_ledger as PL
            if combo is not None:
                # 설화수 = 조합 단위 기록 (buy_one 의 combo_idx 경로와 동일)
                PL.record_combo("현대Hmall", acct_id, combo, order_no=order_no, card=card)
                print(f"[finish] 구매대장 조합 {combo} 기록 (카드 {card})")
            else:
                mf = json.loads((B.ROOT / "cart" / "today_carts.json").read_text(encoding="utf-8"))
                cart = next((c for c in mf.get("carts", [])
                             if c.get("mall") in ("현대", "hmall") and c.get("account") == idx), None)
                n = 0
                for it in (cart or {}).get("items", []):
                    if kws and not any(k in (it.get("name") or "") for k in kws):
                        continue
                    PL.record_food("현대Hmall", acct_id, it.get("product"), qty=it.get("qty"),
                                   order_no=order_no, card=card)
                    n += 1
                print(f"[finish] 구매대장 {n}건 기록 (카드 {card})")
                if n == 0:
                    print("[finish] ⚠️ 대장 0건 — today_carts.json 이 오늘자인지/키워드가 맞는지 확인할 것")
        except Exception as e:
            print(f"[finish] ⚠️ 대장 기록 실패: {e}")
        B.apply_reward_now(idx, kws or None)      # ★적립 (코드가 한다)
        return 0

    if cmd in ("finish_lotte", "finish-lotte"):
        return _finish_lotte(args[1:])

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
    time.sleep(0.6)
    if cmd.startswith("box"):
        return 0 if _verify_box(int(cmd[-1])) else 1
    keys = _CVC_KEYS if cmd == "cvc" else _PIN_KEYS
    return 0 if _verify_len(keys, len(value), "CVC" if cmd == "cvc" else "결제비번") else 1


if __name__ == "__main__":
    sys.exit(main())

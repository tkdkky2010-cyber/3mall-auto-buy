"""현대몰 앱(com.hmallapp) 결제 자동화 — 카드별 흐름 분기.

전제:
  - `adb pair` + `adb connect` 완료된 Galaxy S21+ (1080x2400)
  - PC에서 카트 sync 완료 (구매할 상품이 장바구니에 담겨있음)
  - secrets/card_secrets.json 에 카드별 pin6/card_pw4 저장

카드별 흐름 (CARD_FLOWS):
  - "hyundai_pin"   : 현대 — PIN번호 결제 선택 → 동그라미 → 6자리 → 확인 → 결제하기 → 본인인증(4자리 1회용)
  - "hana_next_pin" : 하나 — 결제하기 → 하나Pay 시트 → '다음' → nFilter 보안키패드(4x4 셔플) → 6자리 → 자동 진행. 4자리 카드비번 없음.
  - 롯데/삼성/BC/NH/KB는 검증 필요.

검증 완료 (2026-05-28):
  - 현대카드 60,340원 승인 (주문번호 20260528114561, 23:45)
  - 하나카드 65,340원 승인 (23:58, 셔플 키패드 ADB tap 가능 확인)

CLI:
    python3 -m phone_auto.hmall_pay 현대        # 전 흐름 실행
    python3 -m phone_auto.hmall_pay 하나 --layout '{"1":[135,1621],"3":[945,1621],...}'  # 셔플 매핑 수동 입력
    python3 -m phone_auto.hmall_pay 현대 --dry  # PIN 입력 직전까지만
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path
from typing import Optional

from phone_auto.adb_input import ADB, load_coords

ROOT = Path(__file__).resolve().parent.parent
SECRETS_PATH = ROOT / "secrets" / "card_secrets.json"
HMALL_PKG = "com.hmallapp"

CARD_FLOWS = {
    "현대": "hyundai_pin",
    "하나": "hana_next_pin",
    "KB":   "kb_kbpay",
}


def load_secrets() -> dict:
    if not SECRETS_PATH.exists():
        raise FileNotFoundError(f"{SECRETS_PATH} not found")
    return json.loads(SECRETS_PATH.read_text())


# ──────────────────────────── 공통 단계 ────────────────────────────

def launch_app(adb: ADB) -> None:
    adb._shell("monkey", "-p", HMALL_PKG, "-c", "android.intent.category.LAUNCHER", "1")
    time.sleep(4)


def close_home_popup(adb: ADB, c: dict) -> None:
    p = c["home_popup_close"]
    adb.tap(p["x"], p["y"])
    time.sleep(1.5)


def open_cart(adb: ADB, c: dict) -> None:
    p = c["cart_icon"]
    adb.tap(p["x"], p["y"])
    time.sleep(3)


def purchase(adb: ADB, c: dict) -> None:
    p = c["purchase_btn"]
    adb.tap(p["x"], p["y"])
    time.sleep(4)


def select_card(adb: ADB, c: dict, card_name: str) -> None:
    """결제수단변경 → 카드 dropdown → 카드 그리드에서 선택."""
    pmc = c["payment_method_change"]
    adb.tap(pmc["x"], pmc["y"])
    time.sleep(2)
    dd = c["card_dropdown"]
    adb.tap(dd["x"], dd["y"])
    time.sleep(2)
    grid = c["card_grid"]
    if card_name not in grid:
        raise KeyError(f"card '{card_name}' not in grid: {list(grid)}")
    g = grid[card_name]
    adb.tap(g["x"], g["y"])
    time.sleep(2)


def click_pay(adb: ADB, c: dict) -> None:
    p = c["pay_btn"]
    adb.tap(p["x"], p["y"])
    time.sleep(4)


# ──────────────────────────── 현대 흐름 ────────────────────────────

def select_pin_payment(adb: ADB, c: dict) -> None:
    """현대 — 앱카드결제 vs PIN번호 결제 중 PIN 선택."""
    p = c["pin_payment"]
    adb.tap(p["x"], p["y"])
    time.sleep(5)


def enter_pin6_fixed(adb: ADB, c: dict, pin: str, delay: float = 0.6) -> None:
    """현대 PIN 키패드 (셔플 X 고정) — 동그라미 영역 tap → 키패드 호출 → 6자리 → 확인."""
    if len(pin) != 6 or not pin.isdigit():
        raise ValueError(f"pin must be 6 digits, got {pin!r}")
    dots = c["pin_dots"]
    adb.tap(dots["x"], dots["y"])
    time.sleep(2)
    pad = c["pin_keypad"]
    for d in pin:
        x, y = pad[d]
        adb.tap(x, y)
        time.sleep(delay)
    conf = c["confirm_btn"]
    adb.tap(conf["x"], conf["y"])
    time.sleep(5)


def enter_auth_pw4(adb: ADB, c: dict, pw: str, delay: float = 0.6) -> None:
    """본인인증 팝업 확인 → 카드비번 필드 tap → 키패드 호출 → 4자리 → 확인. 현대 첫 결제 1회용 추정."""
    if len(pw) != 4 or not pw.isdigit():
        raise ValueError(f"card_pw must be 4 digits, got {pw!r}")
    conf = c["confirm_btn"]
    adb.tap(conf["x"], conf["y"])
    time.sleep(2)
    field = c["auth_pw_field"]
    adb.tap(field["x"], field["y"])
    time.sleep(2)
    pad = c["auth_keypad"]
    for d in pw:
        x, y = pad[d]
        adb.tap(x, y)
        time.sleep(delay)
    adb.tap(conf["x"], conf["y"])
    time.sleep(8)


def run_hyundai(adb: ADB, c: dict, pin: str, pw4: Optional[str], dry: bool) -> None:
    print("[현대 8] PIN payment")
    select_pin_payment(adb, c)
    if dry:
        print("[DRY] stopping before PIN input")
        return
    print(f"[현대 9-11] enter PIN 6digits")
    enter_pin6_fixed(adb, c, pin)
    print("[현대 12] final pay")
    click_pay(adb, c)
    if pw4:
        print(f"[현대 13-14] auth card_pw 4digits")
        enter_auth_pw4(adb, c, pw4)


# ──────────────────────────── 하나 흐름 ────────────────────────────

def click_hana_next(adb: ADB, c: dict) -> None:
    """하나Pay 시트 '다음' 클릭."""
    nb = c["hana"]["next_btn"]
    adb.tap(nb["x"], nb["y"])
    time.sleep(4)


def enter_pin6_shuffled(adb: ADB, c: dict, pin: str, layout: dict, delay: float = 0.6) -> None:
    """하나 nFilter 보안 키패드 — 4x4 셔플. layout={'0':[x,y], '1':[x,y], ...}.

    layout은 매 호출마다 외부에서 결정 (OCR 또는 수동). content-desc 마스킹으로 dump만으론 매핑 불가.
    스크린샷 → 숫자 위치 OCR → layout 생성 → 호출. 미래 ocr_keypad 모듈 통합 예정.
    입력 후 자동으로 결제 진행됨 (별도 '확인' 버튼 없음).
    """
    if len(pin) != 6 or not pin.isdigit():
        raise ValueError(f"pin must be 6 digits, got {pin!r}")
    for d in pin:
        if d not in layout:
            raise ValueError(f"digit '{d}' not in layout. layout keys: {sorted(layout)}")
        x, y = layout[d]
        adb.tap(x, y)
        time.sleep(delay)
    time.sleep(8)  # 자동 결제 진행


def extract_kb_layout_from_dump(adb: ADB) -> dict:
    """KB카드 앱 비번 화면 dump → {'0':[x,y], ...}. FLAG_SECURE라 캡처는 X 지만 dump는 OK + content-desc로 키값 노출."""
    import re
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as f:
        tmp = f.name
    adb.dump_ui(tmp)
    xml = Path(tmp).read_text()
    layout = {}
    for node in re.findall(r'<node[^>]*>', xml):
        m = re.search(r'content-desc="(\d)"', node)
        b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node)
        if not m or not b:
            continue
        cx = (int(b.group(1)) + int(b.group(3))) // 2
        cy = (int(b.group(2)) + int(b.group(4))) // 2
        layout[m.group(1)] = [cx, cy]
    if len(layout) != 10:
        raise RuntimeError(f"KB layout extracted {len(layout)} keys (expected 10): {layout}")
    return layout


def run_kb(adb: ADB, c: dict, pin: str, dry: bool) -> None:
    """KB 흐름: 결제하기 → KB팝업 → KB Pay 결제 → KB앱 결제하기 → 비번 → 입력완료."""
    kb = c["kb"]
    # 결제하기 → KB팝업 등장 (이 단계는 click_pay에서 이미 호출됨)
    print("[KB 8] KB Pay 결제 박스 클릭")
    adb.tap(kb["kbpay_box"]["x"], kb["kbpay_box"]["y"])
    time.sleep(5)
    # KB카드 앱 자동 호출 → 결제하기
    print("[KB 9] KB앱 결제하기")
    adb.tap(kb["app_pay_btn"]["x"], kb["app_pay_btn"]["y"])
    time.sleep(5)
    if dry:
        print("[DRY] stopping before PIN input")
        return
    # 비번 화면 — FLAG_SECURE (캡처X) but dump 가능
    print("[KB 10] extract layout from dump (셔플 OK)")
    layout = extract_kb_layout_from_dump(adb)
    print(f"[KB 10] layout: {layout}")
    print("[KB 11] enter PIN 6digits")
    for d in pin:
        x, y = layout[d]
        adb.tap(x, y)
        time.sleep(0.6)
    print("[KB 12] 입력완료")
    adb.tap(kb["input_done_btn"]["x"], kb["input_done_btn"]["y"])
    time.sleep(8)


def run_hana(adb: ADB, c: dict, pin: str, layout: Optional[dict], dry: bool) -> None:
    print("[하나 8] Hana Pay sheet '다음'")
    click_hana_next(adb, c)
    if dry:
        print("[DRY] stopping before PIN input")
        return
    if not layout:
        # 마지막 알려진 셔플 (참고용 — 다음에 다를 가능성 높음)
        sample = c["hana"].get("_sample_shuffle_2026_05_28", {})
        layout = {k: v for k, v in sample.items() if k.isdigit()}
        print("[하나] WARN: layout 미지정 — 마지막 검증 셔플로 시도 (셔플 변경 시 실패)")
    print(f"[하나 9] enter PIN 6digits (shuffled keypad)")
    enter_pin6_shuffled(adb, c, pin, layout)


# ──────────────────────────── 메인 ────────────────────────────

def run(card_name: str, dry: bool = False, hana_layout: Optional[dict] = None) -> None:
    if card_name not in CARD_FLOWS:
        raise KeyError(f"'{card_name}' 흐름 미정의. 검증된 카드: {list(CARD_FLOWS)}")
    flow = CARD_FLOWS[card_name]

    secrets = load_secrets()
    if card_name not in secrets or not secrets[card_name].get("pin6"):
        raise KeyError(f"'{card_name}' 비번 없음. secrets/card_secrets.json 확인")
    pin = secrets[card_name]["pin6"]
    pw4 = secrets[card_name].get("card_pw4")

    c = load_coords("apps/hmall")
    adb = ADB()
    print(f"[device] {adb.device}  flow={flow}")

    print("[1] launch app");        launch_app(adb)
    print("[2] close home popup");  close_home_popup(adb, c)
    print("[3] open cart");         open_cart(adb, c)
    print("[4] purchase");          purchase(adb, c)
    print(f"[5-6] select card: {card_name}");  select_card(adb, c, card_name)
    print("[7] pay");               click_pay(adb, c)

    if flow == "hyundai_pin":
        run_hyundai(adb, c, pin, pw4, dry)
    elif flow == "hana_next_pin":
        run_hana(adb, c, pin, hana_layout, dry)
    elif flow == "kb_kbpay":
        run_kb(adb, c, pin, dry)
    else:
        raise ValueError(f"unknown flow: {flow}")

    print("[done] 결제 요청 완료. 카드사 알림 확인.")


def _main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    card = args[0]
    dry = "--dry" in args
    layout = None
    for i, a in enumerate(args):
        if a == "--layout" and i + 1 < len(args):
            layout = json.loads(args[i + 1])
            layout = {k: list(v) for k, v in layout.items()}
            break
    run(card, dry=dry, hana_layout=layout)


if __name__ == "__main__":
    _main()

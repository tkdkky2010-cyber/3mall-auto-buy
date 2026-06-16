"""6.16 식품 탭 우측(S열~)에 현대Hmall H.Point 잔액 status 기입 — 1회용.
값 = 이번 세션 조회분(_hmall_point_check.py, 2026-06-16). A:Q(check10 결과)는 건드리지 않음."""
from pathlib import Path
import gspread

ROOT = Path(__file__).resolve().parent.parent
KEY = next(iter(ROOT.glob("gen-lang-*.json")))
SHEET = "1fxB0UvLRy2iQfonCWn5U5mWnXbzSdn6l4e2XuQluhwo"
TAB = "6.16 식품"

# (idx, 계정, 포인트) — _hmall_point_check.py check 결과 (2026-06-16)
POINTS = [
    (1, "tkdkky2002", 225440),
    (2, "1plu1mall1", 222130),
    (3, "kgi5907@daum.net", 156750),
    (4, "1plus1mall0", 126150),
    (5, "1plus1mall1", 145128),
    (6, "1plus1mall4", 1000),
    (7, "linguist01", 75730),
    (8, "ybkim8225", 78310),
    (9, "lee0503031", 80130),
    (10, "jye40323", 56730),
    (11, "kimgaeun04", 19810),
    (12, "Jinhwa4553", 25050),
    (13, "gcd0327", 18540),
    (14, "johwajeong", 1570),
    (15, "jyj041220", 41220),
    (16, "miu1838", 95170),
    (17, "skykow", 47100),
    (18, "ybkim9960", 30520),
    (19, "yeonsuk0428@naver.com", 23380),
]

total = sum(p for _, _, p in POINTS)
payload = [["현대Hmall H.Point 잔액 status (2026-06-16 조회)"],
           ["#", "계정", "포인트(P)"]]
payload += [[i, name, pt] for i, name, pt in POINTS]
payload += [["", "합계", total]]

gc = gspread.service_account(filename=str(KEY))
ws = gc.open_by_key(SHEET).worksheet(TAB)
if ws.col_count < 21:
    ws.add_cols(21 - ws.col_count)
rng = f"S1:U{len(payload)}"
ws.update(values=payload, range_name=rng, value_input_option="USER_ENTERED")
print(f"[OK] {TAB}!{rng} 입력 — {len(POINTS)}계정, 합계 {total:,}P")

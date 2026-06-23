"""6.18 식품 탭 우측(S열~)에 이벤트 H.Point 신청 결과 기입 — 1회용.
이벤트 P202606041140, 2026-06-18 조회 (buy/_hpoint_event_claim.py 검증분). A:O(check10 결과) 미수정."""
from pathlib import Path
import gspread

ROOT = Path(__file__).resolve().parent.parent
KEY = next(iter(ROOT.glob("gen-lang-*.json")))
SHEET = "1fxB0UvLRy2iQfonCWn5U5mWnXbzSdn6l4e2XuQluhwo"
TAB = "6.18 식품"

# (idx, 계정, 신청상태, 행사구매개수, 대상총액, 예상H.Point) — _hpoint_event_claim.py 검증분 (2026-06-18)
ROWS = [
    (1, "tkdkky2002", "기존완료", 3, 150005, 15000),
    (2, "1plu1mall1", "신청완료", 1, 0, 0),
    (3, "kgi5907@daum.net", "신청완료", 1, 0, 0),
    (4, "1plus1mall0", "기존완료", 1, 0, 0),
    (5, "1plus1mall1", "기존완료", 1, 11692, 0),
    (6, "1plus1mall4", "신청완료", 0, 0, 0),
    (7, "linguist01", "신청완료", 0, 0, 0),
    (8, "ybkim8225", "신청완료", 0, 0, 0),
    (9, "lee0503031", "신청완료", 0, 0, 0),
    (10, "jye40323", "신청완료", 0, 0, 0),
    (11, "kimgaeun04", "신청완료", 0, 0, 0),
    (12, "Jinhwa4553", "신청완료", 0, 0, 0),
    (13, "gcd0327", "신청완료", 0, 0, 0),
    (14, "johwajeong", "신청완료", 0, 0, 0),
    (15, "jyj041220", "신청완료", 1, 0, 0),
    (16, "miu1838", "신청완료", 0, 0, 0),
    (17, "skykow", "신청완료", 1, 0, 0),
    (18, "ybkim9960", "신청완료", 1, 0, 0),
    (19, "yeonsuk0428@naver.com", "신청완료", 0, 0, 0),
]

tot_amt = sum(r[4] for r in ROWS)
tot_cnt = sum(r[3] for r in ROWS)
tot_pt = sum(r[5] for r in ROWS)
payload = [["이벤트 H.Point 신청 결과 (P202606041140) — 2026-06-18 조회"],
           ["#", "계정", "신청", "행사구매(개)", "대상총액", "예상H.Point"]]
payload += [[i, name, st, cnt, amt, pt] for i, name, st, cnt, amt, pt in ROWS]
payload += [["", "합계", "", tot_cnt, tot_amt, tot_pt]]

gc = gspread.service_account(filename=str(KEY))
ws = gc.open_by_key(SHEET).worksheet(TAB)
need = 24  # X열까지
if ws.col_count < need:
    ws.add_cols(need - ws.col_count)
rng = f"S1:X{len(payload)}"
ws.update(values=payload, range_name=rng, value_input_option="USER_ENTERED")
print(f"[OK] {TAB}!{rng} 입력 — 19계정, 대상총액 {tot_amt:,}원/{tot_cnt}개, 예상H.Point {tot_pt:,}P")

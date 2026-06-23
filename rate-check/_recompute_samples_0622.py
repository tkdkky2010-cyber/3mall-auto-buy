"""6.22 샘플단가 변경(s35 3000→2500, s36 5000→4500) 반영 — Hmall·롯데 섹션 순/공급률만 재계산.

브라우저 재크롤 X. 샘플단가 무관 컬럼(Hmall 구매가격 / 롯데 최종구매가·적립·소비자가)은 시트값 그대로 두고,
새 추증·GWP(갤러리아가 이미 새 단가로 시트에 기록)만 적용해 총샘플·순·공급률 재계산 후 write-back.
"""
import sys; sys.path.insert(0, 'rate-check')
import _common as C

ws = C.gs_client().open_by_key(C.RATE_SHEET_ID).worksheet('6.22')

# 1) 갤러리아가 시트에 쓴 새 구성 로드 (SoT)
comp = C.load_galleria_composition_from_sheet(ws)
ADD = comp['add_gift_value']
GWP = comp['gwp_6set']
print(f"새 GWP 6세트 = {GWP:,} / 추증 = {ADD}")

def addgift(combo):
    return sum(ADD[c]*q for c, q in combo)

updates = []  # (a1, value)

# 2) Hmall 섹션 (행 59~81): C=소비자가(3) D=추증 E=GWP F=총샘플 K=구매가격(11) L=순 M=공급률
print("\n=== Hmall ===")
hm = ws.get('A59:M81', value_render_option='UNFORMATTED_VALUE')
for off, row in enumerate(hm):
    r = 59 + off
    idx = int(row[0]); combo = C.COMBOS[idx-1]
    소비자 = int(row[2]); 구매가 = int(row[10])
    추증 = addgift(combo); 총샘플 = 추증 + GWP
    순 = 구매가 - 총샘플; rate = round(순/소비자, 4)
    old = row[12]
    updates += [(f'D{r}', 추증), (f'E{r}', GWP), (f'F{r}', 총샘플), (f'L{r}', 순), (f'M{r}', rate)]
    print(f"  #{idx:2} {rate}  (was {old})")

# 3) 롯데 섹션 (행 91~113): C=소비자가(3) D=추증 E=GWP F=총샘플 G=적립(7) H=최종(8) I=순 J=공급률
print("\n=== 롯데 ===")
lt = ws.get('A91:J113', value_render_option='UNFORMATTED_VALUE')
for off, row in enumerate(lt):
    r = 91 + off
    idx = int(row[0]); combo = C.COMBOS[idx-1]
    소비자 = int(row[2]); 적립 = int(row[6]); 최종 = int(row[7])
    추증 = addgift(combo); 총샘플 = 추증 + GWP
    순 = 최종 - 총샘플 - 적립; rate = round(순/소비자, 4)
    old = row[9]
    updates += [(f'D{r}', 추증), (f'E{r}', GWP), (f'F{r}', 총샘플), (f'I{r}', 순), (f'J{r}', rate)]
    print(f"  #{idx:2} {rate}  (was {old})")

# 4) 일괄 write-back
ws.batch_update([{'range': a1, 'values': [[v]]} for a1, v in updates])
print(f"\n✅ 시트 반영 완료 ({len(updates)} cells)")

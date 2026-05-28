"""20조합 재생성 — 후보(≤3종, 70~81만) galleria+lotte 수식 공급률 min 상위20 + h×3 강제.
검증: 현재 COMBOS 재계산이 시트 K(갤러)·M(롯데)와 일치하는지 먼저 확인.
"""
import sys, itertools
sys.path.insert(0, "/Users/jasonkim/Desktop/Vibe Coding/3mall auto buy/rate-check")
import _common as C

gc = C.gs_client()
ws = C.get_or_create_tab(gc.open_by_key(C.RATE_SHEET_ID), C.today_tab_name(), leftmost=True)

# --- 입력값 (시트 SoT) ---
comp = C.load_galleria_composition_from_sheet(ws)
ADD = comp["add_gift_value"]          # {b:16600,...}
GWP6 = comp["gwp_6set"]               # 79200
PRICE = {c: C.PRODUCTS[c]["price"] for c in "bcdefgh"}

# galleria: 기본10% 쿠폰14% (오늘 균일) / 네이버 ×0.978  — 검증으로 확인
G_BASIC = {c: 10.0 for c in "bcdefgh"}
G_COUP  = {c: 14.0 for c in "bcdefgh"}
NAVER = C.GALLERIA_NAVER_MULT

# lotte: 쿠폰 per product, 삼성5%, 페이백1%, 적립 tier
L_COUP = {"b":12,"c":15,"d":15,"e":15,"f":12,"g":15,"h":12}
L_CARD = 5.0
L_PAYBACK = 0.01
L_TIERS = [(50000,7500),(150000,15000)]  # 상품 공통, 조합당 max 1회

def consumer(combo): return sum(PRICE[c]*q for c,q in combo)
def addgift(combo):  return sum(ADD[c]*q for c,q in combo)

def gall_rate(combo):
    소 = consumer(combo)
    gwp = GWP6 if 소>=C.GWP_TIER_70 else (comp["gwp_1set"]*3 if 소>=C.GWP_TIER_40 else 0)
    총샘플 = addgift(combo) + gwp
    final = round(sum(PRICE[c]*q*(1-G_BASIC[c]/100)*(1-G_COUP[c]/100)*NAVER for c,q in combo))
    return (final-총샘플)/소

def lotte_rate(combo):
    소 = consumer(combo)
    총샘플 = addgift(combo) + GWP6
    final = round(sum(PRICE[c]*q*0.9*(1-L_COUP[c]/100)*(1-L_CARD/100)*(1-L_PAYBACK) for c,q in combo))
    applic = [r for c,_ in combo for th,r in L_TIERS if final>=th]
    적립 = max(applic) if applic else 0
    return (final-총샘플-적립)/소

# --- 검증: 현재 COMBOS 재계산 vs 시트 차트 K(갤러)/M(롯데) ---
chart = ws.get("J2:M21")  # idx, galleria, hmall, lotte
sheet_g = {int(r[0]): float(r[1]) for r in chart if len(r)>1 and r[1]}
sheet_l = {int(r[0]): float(r[3]) for r in chart if len(r)>3 and r[3]}
print("=== 검증 (현재 20조합 재계산 vs 시트) ===")
okg=okl=0
for i,combo in enumerate(C.COMBOS,1):
    g=round(gall_rate(combo),4); l=round(lotte_rate(combo),4)
    dg = abs(g-sheet_g.get(i,-9)); dl = abs(l-sheet_l.get(i,-9))
    if dg<=0.0002: okg+=1
    if dl<=0.0002: okl+=1
    if dg>0.0002 or dl>0.0002:
        print(f"  조합{i}: 갤러 계산{g} 시트{sheet_g.get(i)} (Δ{dg:.4f}) | 롯데 계산{l} 시트{sheet_l.get(i)} (Δ{dl:.4f})")
print(f"갤러리아 일치 {okg}/20, 롯데 일치 {okl}/20")
if okg<20 or okl<20:
    print("⚠️ 공식/입력 불일치 — 후보 생성 중단 (먼저 수정)")
    sys.exit(1)
print("✓ 공식 검증 통과 — 후보 생성 진행\n")

# --- 117 후보 생성 + min(갤러,롯데) ---
cands=[]
for r in (1,2,3):
    for codes in itertools.combinations("bcdefgh", r):
        for qtys in itertools.product(range(1,7), repeat=r):
            combo=tuple(zip(codes,qtys)); tot=consumer(combo)
            if 700000<=tot<=810000:
                g=gall_rate(combo); l=lotte_rate(combo)
                cands.append({"combo":combo,"소":tot,"g":g,"l":l,"min":min(g,l)})
cands.sort(key=lambda x:x["min"])
print(f"=== 후보 {len(cands)}개 — min(갤러,롯데) 오름차순 상위 25 ===")
for x in cands[:25]:
    cl=C.combo_label_ko(list(x["combo"]))
    print(f"  {x['min']:.4f}  (갤{x['g']:.4f}/롯{x['l']:.4f}) 소{x['소']:,}  {cl}")

# --- 상위 20 + h×3 강제 ---
H3=(("h",3),)
top20=[x["combo"] for x in cands[:20]]
if H3 not in top20:
    h3rank=next((i for i,x in enumerate(cands) if x["combo"]==H3),-1)
    print(f"\n  h×3 강제 포함 (현재 순위 {h3rank+1}위) → 20위 교체")
    top20=top20[:19]+[H3]
print("\n=== 최종 20조합 (재생성) ===")
for combo in top20:
    print("   ", list(combo), "  ", C.combo_label_ko(list(combo)))
# COMBOS 형식 출력
print("\n=== _common.py COMBOS 형식 ===")
print("COMBOS = [")
for combo in top20:
    print("    " + repr([list(t) for t in combo]) + ",")
print("]")

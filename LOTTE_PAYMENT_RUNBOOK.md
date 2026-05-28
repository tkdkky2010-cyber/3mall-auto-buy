# 롯데홈쇼핑 폰앱 결제 RUNBOOK (교과서)

**2026-05-27 END-TO-END 라이브 결제 성공 루트.** 다음에도 이 루트 그대로 따르면 성공.
주문번호 예시 `2026-05-27-A39016` (삼성카드, 392,870원 → 5% 청구할인 373,230원).

> 이건 "이렇게 하면 성공한다"의 정확한 기록이다. 좌표·판단·함정 전부 실측. 추측 금지.
> 코드: `phone_auto/flow_runner.py` 핸들러 + `phone_auto/coords/apps/lotte_homeshopping.json`.

---

## 0. 전제 / 환경

- **ADB 무선 연결** (`adb devices` 로 device 1개). cart/checkout 화면은 ADB screencap OK (FLAG_SECURE 아님).
- **iPhone Continuity 카메라** = PIN 단계에서만 필수. 폰 가로 마운트 + AVFoundation rotation 90 = portrait 1080x1920.
  - ⚠️ 사용자가 폰 만지면 카메라 빠짐("나가리", AVF device 0). **PIN 직전 `AVCaptureDeviceTypeContinuityCamera` 잡히는지 확인.**
  - iPhone 은 `External` 아니라 **`ContinuityCamera` type** 으로 enumerate (이거 빠뜨리면 device 0).
- python3 = framework 3.13 으로 AVFoundation 실행됨 (SIGKILL 안 남, 5/27 확인).
- **PIN = 137601** (전 카드사 공통, 6자리).
- 계정 = 폰에 로그인된 것 (5/27 = 김건엽). 카트는 **비운 상태에서 시작**.

## 0-1. ⏱️ 5분 결제 타임아웃 (★중요)

**`결제하기` 눌러 삼성카드/monimo 진입한 순간부터 5분 내 PIN 완료 못 하면 세션 만료(나가리).**
→ Phase 4 는 빠르게. PIN 은 멀티프레임 voting 말고 **1프레임 캡처 직탭** (아래 방식).

---

## Phase 1 — 장바구니 담기 (상품별 수동/관찰, 아직 미자동화)

상품 = 설화수 letter-code (`_common.py` PRODUCTS, lotte goods_no = `hsmaster/config/sulwhasoo-ids.json`):

| code | 이름 | lotte goods_no |
|---|---|---|
| b | 윤조3종 (에센셜 퍼스트케어) | 2923416935 |
| c | 자음2종 (에센셜 데일리) | 2923389602 |
| d | 본윤2종 (설화수맨) | 2008758498 |

**각 상품 반복:**
1. 검색: 상세화면 우상단 **검색 Q (880,150)** → 검색 입력창 **(323,158)** tap → `adb shell input text <goods_no>` → `KEYCODE_ENTER`. (goods_no 직검색 = 상품 detail 직진입, 결과 list 안 거침)
2. **쿠폰받기 (541,1861)** → 쿠폰 모달 → OCR **"쿠폰 전체 다운로드"** tap (★ y 가변: b=1102, c/d=1672 — OCR 동적 필수, 하드코딩 금지)
3. **"쿠폰발급 완료" 확인 팝업 × (663,~1063)** 닫기 → 모달 닫기 **× (1010,155, 상단우측 고정)**
4. **구매하기 (698,2154)** → 옵션 모달 (타입선택 이미 펼침)
5. **옵션 "세트" tap** — ★ OCR + **"(품절)" 필터 필수**. d=본윤은 첫 옵션들이 "세트(지류상품권…)(품절)", 정상 "세트" 는 따로 (94,1315). b/c 는 단일 "세트" (94,1067).
6. 수량 기본 1 → **장바구니 (207,2117)** → "장바구니에 담겼습니다" 배너 확인

---

## Phase 2 — 카트 → checkout (flow_runner 핸들러, 검증됨)

1. **장바구니 icon (965,152)** (≈950 도 카트. 검색은 카트 **왼쪽**)
2. **`lotte_cart_select_all`**: 헤더 = **"일반 (n/m)"** (★ `== "일반"` 정확매칭 X, substring). 전체선택 체크박스 = 행 **좌측 끝 절대 (60,303)** (텍스트 "(n/m)" 가 폭 변동 → 상대 offset 금지). (n/m) regex 로 self-verify + retry. → (0/3)→(3/3).
3. swipe up `(540,1800)→(540,600)` 로 주문하기 노출
4. **`tap_then_expect` text="주문하기" expect_text="결제하기"**: 주문하기 (613,2152) **단 1회** tap → checkout 의 "결제하기" 등장 검증. 미전환=팝업 → dismiss 후 1회만 재tap. (★ "주문하기 2번 눌러 동의없이 결제 alert 직행" 방지)
   - cart 버튼 텍스트 = "주문하기 (n건 …원)", checkout 버튼 = "…원 결제하기".

## Phase 3 — checkout 설정 (flow_runner 핸들러, 검증됨)

순서: **주소 → 할인쿠폰 → 플러스쿠폰 → 카드 → 동의** (memory: 이 순서 고정)

3-1. **`lotte_change_address` ("화곡동 890")**:
- 배송정보 헤더 우측 **expand "~" (999,312)** tap → 펼침
- **주소 "변경 >" (970,715)** — ★ 우측정렬 short. "배송방법 변경(픽업서비스)"(중앙,긴텍스트)와 구분: `"변경" 포함 & "배송방법"/"픽업" 제외 & 최대 cx`
- 주소 목록에서 **"화곡동 890" 포함 주소 (517,1255)** tap. (저장주소 2개, "강서로5길 50 (화곡동, …)" = 잘못, "…앞 (화곡동 890, …)" = 정답. 연락처도 자동 변경됨)

3-2. **`lotte_apply_coupons` section="할인쿠폰"** (10% 고정):
- 섹션 "할인쿠폰(3)" tap → **"변경 >" (≈942, 우측정렬)** tap (★ 안내문 "변경 버튼을 클릭하여…" 와 구분: `"변경" & "버튼" 제외 & 최대 cx`). 이미 선택돼 변경 보이면 섹션 재탭 금지(toggle 해제됨).
- 할인선택 화면, 상품 dropdown 3개 @ (419,758)(418,1231)(419,1707)
- 각 dropdown tap → **중앙 모달 "할인선택"** (닫기 등장까지 poll) → 옵션 = 닫기 위 "<n>%" 행 → 즉석쿠폰 10% tap → 모달 자동닫힘 → 합계 변화
- **선택완료 (789,2164)**. → 할인 50,400 (=10%×3)

3-3. **`lotte_apply_coupons` section="플러스쿠폰"** (상품별 가변):
- 동일 흐름. ★ **최고 % 선택** — 상품마다 15/14/12 혼재, 위치순 X.
- ★ **모달 안 닫히면 = 그 % 미적용(다른 상품용) → 다음 % 시도.** 5/27 결과: 윤조 12% / 자음 15% / **본윤 14%** (본윤은 15·12% 보임에도 14%만 적용). → 플러스 60,730
- (모달은 항상 같은 중앙위치이지만 dropdown 위치별로 살짝 다름 → 닫기 button 위쪽만 옵션으로, underlying 행 % 라벨 오인 금지. **반드시 모달 완전히 열린 뒤(닫기 등장) OCR.**)

3-4. **`lotte_change_card` ("삼성카드")**:
- 당일 할인카드 banner = **"삼성카드(신용카드/L.PAY) 5% 할인 (5만원↑)"** → card_name="삼성카드" (★ silent default 없음, 반드시 vars/`--card` 로 지정).
- **"다른 결제수단" radio (x=80, cy=OCR)** → **신용카드** → dropdown "선택 해주세요" → **삼성카드** option. (배너 텍스트에도 "삼성카드" 있지만 dropdown option 정확히 잡음). 결과: 카드선택 삼성카드 / 일시불.

3-5. **`lotte_tap_agreement`**: "주문 내역 확인 동의(필수)" → 체크박스 좌측 **(64,1800)** tap.

**금액 검증**: list 504,000 → 할인쿠폰 -50,400 = 453,600 → 플러스 -60,730 = **392,870** (결제하기 표시). 삼성 5%는 청구할인(→373,230 billed).

## Phase 4 — 결제 + PIN (수동/관찰, 카메라 필수, ★5분 내)

1. **결제하기 (539,2173)** → "삼성카드 결제수단" 모달
2. **"monimo pay 결제" (508,734)** (★ 삼성=monimo 경로. 삼성카드앱/오픈앱카드/간편결제 아님)
3. monimo `MonimoPayPayActivity` (화면 보임, ADB OK). 카드 = American Express Reserve(삼성 신용카드, 5% 대상). 금액 "LOTTE Homeshopping에서 392,870원".
4. **결제하기 (540,2130, dump_text "결제하기")** → `MonimoPayVerifyActivity` = **PIN 키패드 (FLAG_SECURE, ADB screencap 검정 0.0)**
5. **PIN 입력 (셔플 6자리, 재셔플 없음):**
   - `capture_portrait_frame()` 1프레임 → `ocr_text` 로 0~9 cam 좌표 (10개 다 잡힘)
   - cam cols 3개 / rows 4개 → **검증된 monimo 버튼그리드 phone 좌표** 매핑:
     `COL_X=[200,540,880]`, `ROW_Y=[1583,1765,1947,2129]` (cam cluster index 순서대로)
   - **137601** 각 digit phone좌표 직탭, **매 tap 0.5초 페이싱**. (멀티프레임 voting 불필요 — 1캡처로 충분, 빠름)
6. → lotte `SubActivity` 복귀 → **"주문완료" + 주문번호** 확인 (PIN 틀리면 verify 화면 잔류)

---

## 함정 / 실패모드 (이번에 실제로 고친 것들)

| 증상 | 원인 | 해결 |
|---|---|---|
| 전체선택 안 됨 | 헤더가 "일반 (0/3)" 인데 `=="일반"` 매칭 | substring + 절대 (60,303) |
| 주문하기 2번 눌러 결제 alert | sleep만 의존, 전환 검증 없음 | tap_then_expect (단일+전환검증) |
| 동의 팝업에서 멈춤 | 팝업 방어 없음 | dismiss_alert_if_present (확인/닫기) |
| 주소 대신 픽업 변경 누름 | "변경" 이 "배송방법 변경"에도 있음 | "배송방법"/"픽업" 제외 + 우측정렬 |
| 쿠폰 변경 대신 안내문 누름 | "변경" 이 안내문에도 있음 | "버튼" 제외 + 우측정렬 |
| 쿠폰 dropdown 0개 | 모달 전환 0.5s 너무 빠름 | 닫기 등장까지 poll |
| 다른상품 % 라벨 오인 | 모달 덜 열린 채 OCR | 모달(닫기) 열린 뒤 + 닫기 위쪽만 |
| 본윤에 15% 적용 실패 | best % 가 상품별로 적용 불가 | 모달 안닫히면 다음 % 시도 |
| 카드 잘못 결제 | silent '삼성카드' default | default 제거, 반드시 지정 |
| portrait 캡처 device 0 | iPhone = ContinuityCamera type | type 목록에 추가 |
| `re` NameError | lotte_apply_coupons 중복 import re 가 함수전체 가림 | 중복 제거 |
| 결제 만료 | 5분 초과 | PIN 1프레임 직탭으로 빠르게 |

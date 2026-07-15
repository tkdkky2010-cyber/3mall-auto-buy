# 토스페이(Toss Pay) 결제 경로 노트 — 2026-06-01 탐색 + **2026-07-15 pay_toss 코드화·라이브검증 완료(#7)**

> ## ✅ 구현 완료 (2026-07-15) — 다음 토스 할인날엔 그냥 `python3 buy.py 현대 N` (자동)
> `phone_auto/hmall_hyundai_buy.py`의 **`detect_card`(토스 우선인식) + `_select_toss_card` + `pay_toss`** 로
> end-to-end 자동. buy_one 이 당일카드=토스 감지하면 자동 라우팅. #7 라이브 완주(결제+완료탭+적립).
>
> **★대전제: 토스앱(`viva.republica.toss`)이 로그인돼 있어야 함.** 미로그인이면 게스트 본인확인
> (휴대폰번호+SMS/PASS)이 떠 **자동화 불가** → pay_toss가 `CertifyGuest`/'휴대폰 번호' 감지해 안전정지(미결제).
> 세션 전 토스앱 열어 로그인 확인(잠금 PIN=137601로 열림 = 로그인 상태). 카드=토스 기본카드(현재 Amex) 그대로 결제.
>
> **검증된 전체 경로 (결합 할인날, 2026-07-15)**:
> ```
> 주문서 카드할인 '토스페이 삼성 N% 즉시할인' 카드박스 직접 탭(_select_toss_card; 700px 캐러셀 정본은
>   토스 레이아웃 미지원 '캐러셀 None' → 카드박스 탭 + 결제버튼금액==토스카드금액/‘적용되었어요’ 토스트 검증)
> → 원 결제하기(OCR) → 토스 '결제진행' 화면 '다음'(OCR) → 토스앱 OnlinePayActivity(screencap O=OCR)
> → '결제하기'(OCR) → PIN PasswordActivity(FLAG_SECURE, 셔플, **text="N" 노드** → `input_pin source=text_dump` 137601)
> → ★OnlinePayApproveCompleteActivity '현대Hmall에서 결제를 완료해주세요' → **'완료' 탭**(안 누르면 카드 승인됐는데
>   hmall 주문 미생성!) → hmall 복귀 = 주문완료. 이후 buy_one 공통(wait_order_complete + 적립).
> ```
> - **PIN은 `source=text_dump`** (❌`dump`=content-desc 아님). 토스 셔플 키패드 숫자는 `text` 노드. PIN=137601.
> - '토스페이 삼성 N%'의 '삼성'은 hmall-side 프로모명일 뿐 — 실제 청구는 토스 기본카드(Amex)이고 7%는 그대로 적용됨(#11 실측).
> - 취소: 토스 화면 back → '결제 취소할까요?' → '결제 취소' → hmall 복귀(미결제, 카트 보존).

토스페이 = **간편결제 채널**이 신용카드를 감싸는 방식 (직접 카드 아님). 카카오페이는 다른 폰에서 사용 중이라 제외, 토스만 사용.

## 두 가지 시나리오
- **결합 할인날** (미관찰): 캐러셀에 `토스페이 OO카드 할인`(예: 토스페이 삼성카드) 표시 → 누르면 **hmall이 토스페이+해당카드까지 자동선택 완료** → 토스앱 진입 시 그 카드로 바로 → 결제 자연스럽고 간단.
- **비할인날 (오늘 관찰)**: 토스페이 단독만 선택 가능(토스 단독할인 없음) → 토스앱 안에서 **카드 직접 선택** 후 결제.

## 진입 경로 (오늘 실측, OCR 라벨은 안정적)
```
주문서 → 결제수단변경 → '페이/Pay' 탭 → '토스페이' 선택 → 원결제하기(결제하기)
→ '결제진행' 화면 '다음' → 토스앱 진입
```
- 토스앱 패키지: **`viva.republica.toss`**
  - 결제화면: `im.toss.features.payment.ui.online.activity.OnlinePayActivity`
  - PIN화면: `viva.republica.toss.password.PasswordActivity`
- '페이/Pay' 탭의 페이 그리드(2행): H포인트페이/네이버페이/삼성페이 · 카카오페이/**토스페이**/페이코 · 스마일페이/폰페이.

## 토스앱 결제화면 (OnlinePayActivity)
- **screencap 정상(FLAG_SECURE 아님) → OCR 가능.**
- 기본 선택카드 표시 + `결제수단 변경 • 설정` → 카드목록(✓=현재선택, 탭해서 변경) → `결제하기`.
- 등록 카드 예: American Express, 국민카드(여러장), 현대카드 등 + '카드 추가하기'/'모두 보기'.
- 하단 `개인(신용)정보 제3자 제공 동의 필수 항목에 동의합니다`.

## ★ PIN 화면 (PasswordActivity) — 핵심
- **screencap = FLAG_SECURE (검정, OCR 불가).** ⚠️ 삼성/하나처럼 OCR 하면 안 됨.
- **BUT `uiautomator dump` 는 됨** → 숫자가 `text="N"` 노드 + `bounds`로 다 잡힘 (KB/롯데/NH앱과 동일 패턴).
- **6자리 셔플** — 숫자 위치가 매번 바뀜.
- → **`input_pin source=dump`** 사용 (결제 순간마다 dump를 **라이브로 새로 읽어** 그때의 숫자→bounds 매핑으로 탭).
  - ⚠️⚠️ **숫자별 좌표 하드코딩 절대 금지** (셔플이라 다음엔 틀림). dump 라이브 읽기만.
  - 키패드 그리드 구조(셀 위치는 고정, 숫자만 셔플): **3열 × 4행**, 4행은 가운데 1칸. 셀 영역 ≈ x[34~1046], y[1316~2237].
- PIN 값 = **137601** (다른 카드와 동일).

## 취소 방법 (탐색/중단 시)
토스 결제화면에서 back → `결제 취소할까요?` 다이얼로그 → **`결제 취소`** 탭 → hmall 복귀(결제 안 됨, 카트 보존).

## pay_toss 구현 완료 (2026-07-15, 라이브검증 #7) — 위 '✅ 구현 완료' 박스가 정본
실제 코드(`phone_auto/hmall_hyundai_buy.py` `pay_toss`/`_select_toss_card`/`detect_card` 토스분기,
`flow_runner.py` `input_pin source=text_dump`, `hmall_webview.py` `_webview_socket` hmall PID 우선)로 구현·검증.
- 2026-06-01 탐색 때 예상했던 것과 다른 실측 3건: ① 할인날 캐러셀은 **자동선택 안 됨**(카드박스 직접 탭 필요),
  ② PIN이 content-desc 아닌 **text 노드**(text_dump), ③ PIN 뒤 **'완료' 탭**(승인완료 화면)이 있어야 hmall 주문 생성.
- ⚠️ 실패사례: `_webview_socket`이 카드앱(KB 등) 백그라운드 웹뷰 소켓을 먼저 잡아 login 타임아웃 → hmall PID 우선으로 fix.
  `_select_toss_card` btn/toss 금액 OCR 실패 시 SELECT_CARD_FAIL(transient) → **재시도로 해결**(#10 실측).

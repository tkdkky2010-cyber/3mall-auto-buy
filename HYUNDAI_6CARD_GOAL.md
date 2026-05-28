# GOAL: hmall(현대H몰) 1번 계정 c제품 1개 결제로 카드 5종 폰 자동화 flow JSON 라이브 완성

## 배경 / 왜 이 작업이 필요한가
3몰 무인 자동구매 진척도를 검증한 결과, 카드앱 PIN 자동 입력은 **삼성 monimo (cardCd02) 1종만 5/27 라이브 검증 완료**. 나머지 카드앱들은 좌표·flow 가 미완성 상태(상세는 ‘카드별 현재 상태’ 항).
NH(cardCd40) 는 사용자가 카드 자체 차단으로 "막혀서 안 됨" 명시 → 영구 제외.
이번 세션 목표: **hmall 1번 계정 + 설화수 c(자음2종) 1개**를 단일 SKU 테스트 베드로 두고, 카드 **5종 (BC·현대·롯데·하나·KB)** 결제 코드 발급 → 폰 카드앱 자동 PIN 입력 라이브 검증 → flow JSON 의 TODO·placeholder 모두 실측으로 채워서 다음에 무인 실행되게 한다. **삼성카드 = 이미 완료 (5/27 라이브, 회귀 X), 신한카드·NH 농협카드 미포함**.
사용자가 ADB 무선 연결 + iPhone Continuity 카메라 + 1번 계정 로그인된 CDP Chrome 을 직접 준비함. 실결제 허용 — 성공 시 사용자가 hmall mypage 에서 직접 주문 취소.

## 식별 / 자원
- **몰:** hmall = 현대H몰 (hmall.com). `hsmaster/src/malls/hyundai.ts` 의 `HyundaiMall` 클래스가 실제로는 hmall.com 을 타겟함 (hsmaster 내부 네이밍이 'hyundai' 인데 의미는 현대H몰). thehyundai.com (현대백화점) 아님.
- **상품:** c = 자음2종 (에센셜 데일리 세트), hmall slitmCd = **2228722509** (출처 `hsmaster/config/sulwhasoo-ids.json` 의 `ids.c.hyundai`). 수량 1.
- **계정:** hmall #1 — `hsmaster` `loadAccounts('hyundai')[0]` (위와 같이 'hyundai' 키 = hmall). 세션은 사용자가 CDP 9222 Chrome 에서 로그인 상태로 띄워둠.
- **공통 6자리 카드 PIN:** `137601` (모든 카드사, 메모리 [[reference_card_pins]])
- **폰 환경:**
  - ADB 무선 1대 (`adb devices` 출력 1개)
  - iPhone Continuity 카메라 (PIN 셔플 키패드 + FLAG_SECURE 카드앱용). AVFoundation `videoRotationAngle=90` portrait 1080×1920 (메모리 [[feedback_phone_landscape_mount_cam_portrait]], `phone_auto/_tmp/method5_focus.py` 참고).
  - ESP32 는 이번 세션 미사용 (ADB 무선 + Continuity 로 풀 커버). UART 가드(메모리 [[feedback_esp32_phone_only]])도 비적용.

## 대상 카드 5종 (삼성=완료, 신한·NH 제외) — 카드별 현재 상태 / 해야 할 일
| # | 카드 | cardCd | flow JSON | 현재 상태 | 이번 세션에 할 일 |
|---|---|---|---|---|---|
| 1 | BC 페이북/ISP | 01 | `bc_paybook_isp.json` | flow_payment 17 step / TODO 2개 (`키패드 완료` / `결제하기` 좌표) / placeholder 교체 완료(5/28). PIN 6자리 ✓, 코드 입력 화면 도달까지 검증됨 | TODO 좌표 실측 + 7자리 OCR preset/handler 추가, 광고 modal close 좌표 실측 |
| 2 | 현대카드 | 04 | `hyundai_card.json` | `flow_payment` 배열 비어있음 (좌표·buttons·pin_screen 만 5/22 캡쳐). 코드 추출 검증 ✓ (5/28 modal fallback) | `flow_payment` 신규 작성 (앱 진입 → 광고 close → PIN 우회 → 앱카드 탭 → 코드 입력 → 결제). PIN 셔플 매 진입 dump+OCR 재매핑 |
| 3 | 롯데카드 | 08 | `lotte_card.json` | flow_payment 11 step / `<7자리 결제비번>` placeholder + 마지막 `TODO: 코드 입력 후 화면 dump` | TODO 단계 (결제하기 좌표) 실측 채우고 placeholder 를 `--code` 변수로 교체. 광고 modal close 좌표 실측 |
| 4 | 하나 | 10 | `hana_card.json` | flow_payment 14 step / `<7자리 코드>` placeholder + `TODO: 다음 button 좌표` / FLAG_SECURE (카메라 OCR 필요) | TODO 좌표 실측 + placeholder 교체. screencap 검정이면 Continuity 카메라 frame 으로 대체 |
| 5 | KB Pay | 03 | **파일 없음** | 처음부터 | `kb_pay.json` 신규 생성 — 앱 진입·광고 modal close·결제코드 입력·PIN 셔플 키패드 전부 실측 캡쳐 |

## 작업 순서 (한 카드 = 한 사이클, 총 6 사이클)
각 카드 c1~c6 마다 아래 9단계 반복. **카드 간 텀 없이 백투백 진행** (앞 카드 사이클 끝나는 즉시 다음 카드 사이클 시작, 사용자 주문 취소도 비동기로 부탁만 띄우고 다음 사이클로 넘어감). 단, hmall 이 부과하는 결제코드 5분 유효시간은 한 사이클 안에서 PIN 입력까지 완료해야 함 (우리가 두는 wait 가 아니라 mall 의 deadline).

1. **사전 점검** (사이클 시작 직전마다):
   - `adb devices` → 1개 device 확인. 없으면 STOP, 사용자에게 재연결 요청.
   - Continuity 카메라 enumerate → `AVCaptureDeviceTypeContinuityCamera` 잡히는지 확인 (메모리 [[project_lotte_flow_payment_status]] 의 PIN-단계 카메라 빠짐 사례 참고). 빠지면 STOP.
   - `chrome_launcher.ensure_chrome(9222)` 로 CDP Chrome alive 확인.
2. **카트 비우기** (메모리 [[feedback_cart_clear_must_succeed]] — clear 성공 보장 후 add).
3. **c제품 add** (HyundaiMall.addToCart('c', 1) 또는 `slitmCd=2228722509` 직접). 옵션 단일.
4. **hmall checkout 진입 → 카드사 선택 → 결제하기**
   - `HyundaiMall` (= hmall 핸들러) 에 결제 함수가 없으므로 이 사이클의 컴터쪽은 **신규 구현 필요**. 새 메소드 `HyundaiMall.checkoutAndGetPayCode(cardCdHint: string): Promise<{code: string, ms: number}>` 또는 별도 `buy/hmall_checkout.py` 추가. 셀렉터는 라이브 DOM dump 로 도출(추측 금지). DRY 옵션 지원하되 이번 세션은 DRY=false 로 실결제. ※ 기존 `buy/run.py` 가 이미 Hmall 결제 코드 추출 흐름을 갖고 있으니 재사용 검토 우선.
   - 카드 선택 UI 가 캐러셀이면 `cardCd0X` 매핑, dropdown 이면 `<li value="...">` 매핑 — 실측으로 확정.
5. **7자리 결제코드 자동 추출** — DOM querySelector 우선, 실패 시 화면 OCR fallback. 사람이 받아쓰기 절대 X. 추출 즉시 `time.time()` 기록 (5분 타임아웃 카운트다운 시작).
6. **폰 카드앱 자동 진입 + 라이브 dump 채우기**
   - `python3 -m phone_auto.flow_runner <card_name> flow_payment --pin=137601 --code=<7자리>` 실행.
   - 각 step 실행 전후 `adb shell uiautomator dump` + screencap (또는 Continuity frame) 으로 좌표 실측, OCR 매핑.
   - 미완 좌표·TODO 발견되는 즉시 **그 자리에서 dump→tap 위치 도출→JSON patch 후 재실행** (메모리 [[feedback_phone_coord_no_estimate]] / [[feedback_real_debug_no_guess]]). `verified=True` 좌표만 채택.
   - 광고 modal 처리는 매 카드앱 진입 직후 1단계로 강제 (메모리 [[feedback_card_app_popup_close]]).
   - PIN/결제코드 매 키 입력 사이 **0.5초+ wait** 강제 (메모리 [[feedback_card_pin_input_pacing]]).
   - 키패드 셔플 카드(BC/롯데/현대 등)는 매 키 입력마다 dump+OCR 재매핑 (한 번 매핑 후 재사용 금지).
   - FLAG_SECURE (hana 등) 면 screencap=검정 → Continuity 카메라 frame 으로 키패드 OCR 우회.
7. **결제 완료 검증 → hmall mypage 결제완료 카운트 증가 확인** (구현 있으면 사용, 없으면 사용자 육안 확인 OK).
8. **사용자에게 주문 취소 요청 (비대기)** — 카드사 한 사이클 종료 시 "hmall mypage 에서 방금 주문 취소해 주세요" 메시지 출력만 하고 **대기 없이 다음 사이클로 즉시 진입**. 취소는 사용자가 백그라운드로 처리.
9. **flow JSON / hmall 결제 함수 commit** — 사이클 끝날 때마다 surgical commit:
   - 메시지 예: `phone_auto/bc_paybook_isp: TODO 3개 좌표 실측 채움 (1번 hmall 라이브 검증)`
   - 메모리 [[feedback_focused_commits]] — PM in-flight 변경과 섞지 말고 명시적 stage.

## Acceptance Criteria
세션 종료 시 다음 모두 충족:
- [ ] BC/현대/신한/롯데/하나/KB 각각 hmall 1번 계정으로 7자리 결제코드 발급 도달 + 폰 카드앱 PIN 자동 입력 → 결제 완료 (또는 마지막 도달 단계 명확히 로그)
- [ ] 위 6개 카드 flow JSON 의 모든 `TODO` / `<placeholder>` 제거
- [ ] `shinhan_card.json`, `kb_pay.json` 신규 파일 생성
- [ ] `buy/run.py` 의 `CARD_CD_TO_APP_HANDLER` 에 cardCd03(KB), cardCd07(신한) 매핑 추가 — NH(cardCd40) 만 미완 주석 유지
- [ ] thehyundai checkout 결제코드 발급 함수 신규 구현 (hsmaster TS 또는 buy/ python — 기존 코드 스타일 매칭)
- [ ] 각 카드별 결과를 표로 정리 (단계 도달 / 라이브 검증 OK·실패 사유)

## 가드레일 (위반 시 사용자가 같은 지적 두 번 안 하게 — 메모리에서 자동 로드)
- **추측 금지**: DOM·키패드 위치 모두 라이브 dump/OCR 후 결정. 셔플 키패드는 매 입력마다 재매핑. (메모리 [[feedback_real_debug_no_guess]], [[feedback_phone_coord_no_estimate]])
- **카트 비우기 실패 시 add 진행 금지** — 빈 카트로만 시작 (메모리 [[feedback_cart_clear_must_succeed]]).
- **광고 modal X 매번 OCR로 닫기 — 모든 카드사 공통**. 카드앱 진입 후 첫 step. OCR 로 "×" / "X" / "닫기" / "오늘 그만 보기" 등 표시 찾아 tap. 좌표 하드코딩 금지 — 광고는 매일/매회 다름. (메모리 [[feedback_card_app_popup_close]])
- **잘못 tap 시 폰 nav `<` (KEYCODE_BACK) 으로 즉시 복구** — 모든 카드사·모든 작업 공통. 화면 잘못 진입 감지 시 (expect_text 미발견 등) back 1~2회 → 직전 화면 복귀 후 좌표 재시도. flow JSON `tap_until_text` 의 fallback path 에 active 권장.
- **PIN/코드 입력 0.5초+ 페이싱** — 메모리 [[feedback_card_pin_input_pacing]].
- **CDP attach 전 `chrome_launcher.ensure_chrome(9222)` 필수** — 메모리 [[feedback_chrome_auto_launch]].
- **결제 실패/중단 시 stale hmall checkout 페이지 close** — vpay popup 누적되면 다음 카드 cycle 시 직전 코드를 stale grab 함. 매 cycle 시작 시 stale `/oda/order` / `vpay.co.kr` tab close 강제.
- **focused commit** — 카드 1종 = 1 commit, 다른 in-flight 변경 섞지 말 것.
- **자동 진행, 중간 질문 금지** — 한 명령으로 6 사이클 run, self-recover. 사용자 개입은 ① 주문 취소 ② 폰 끊김 복구 ③ 카드앱 PIN 잘못 입력 시뿐 (메모리 [[feedback_automation_first]]).

## 예상 소요 시간
- 카드 1종 사이클 = 결제 진입 + 폰 PIN 입력 + dump 보정(가변, 첫 시도일수록 길어짐). 우리쪽 추가 wait 없이 최대한 빠르게.
- 6종 총합 = 컴터·폰 idle 없이 백투백 진행, 카드 간 텀 0. 주문 취소는 백그라운드(사용자 처리 대기 안 함, 다음 사이클로 즉시 진입).
- 정확성 > 속도이긴 하지만, 정확성 깨지지 않는 한도 내에서 최대속도.

## 명시적으로 안 하는 것 (scope creep 방지)
- NH 카드 작업 (사용자 차단으로 영구 제외)
- Samsung monimo 재검증 (이미 완료)
- 갤러리아/롯데/Hmall 자동화 (이번 범위 외)
- 토스페이·카카오페이 flow (사용자가 별도 카드 우회로 처리)
- 11종 조합·다계정 확장 (1번 계정 단일 SKU 만)
- thehyundai 결제 함수의 풀 production hardening (이번엔 라이브 검증 1회면 OK, 다회 안정성은 후속)

## 산출 보고 양식 (세션 끝에)
| 카드 | 7자리 코드 발급 | 폰앱 진입 | PIN 입력 | 결제 완료 | flow JSON 갱신 |
|---|---|---|---|---|---|
| BC | ✅/❌ | … | … | … | … |
| 현대 | … | … | … | … | … |
| 신한 | … | … | … | … | … |
| 롯데 | … | … | … | … | … |
| 하나 | … | … | … | … | … |
| KB | … | … | … | … | … |

실패 시 마지막 도달 step 과 원인(좌표 미발견/OCR 실패/세션 만료/카드앱 update 등) 기록.

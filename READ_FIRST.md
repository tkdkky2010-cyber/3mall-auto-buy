# ⚠️ 작업 시작 전 필독 (AI/Claude 규칙)

**어떤 작업이든 시작하기 전에, 먼저 이 폴더 전체를 읽어 전체 흐름을 파악한 뒤 작업을 시작한다.**
흐름을 모른 채 코드를 건드리거나 새로 만들면, 이미 결정·구현된 것을 모르고 헤매거나 중복/오류를 만든다.

## 왜 (실제 사고 사례)
- 2026-06-01: 어제 `phone_auto/hmall_hyundai_buy.py`에 **7개 카드사 전부 SDK + 당일카드 자동감지(`detect_card`/`CARD_ALIASES`) + `select_card` 라우팅**을 만들어 놨는데,
  그걸 안 읽고 롯데홈쇼핑을 "롯데카드 전용"으로만 보고 비롯데 당일카드 앞에서 헤맴. → **전체를 안 읽어 흐름을 몰랐던 것**이 원인.
- 2026-06-06: 전날 커밋된 주문서 포인트/카드선택 **700px 정본**(`flow_runner.py`)을 안 보고 설화수 경로에 OCR 방식을 중복 구현 →
  설화수 16건 **포인트 미사용** 결제 + #17 카드선택 오진. → 같은 원인.

## 시작 전 반드시 읽을 것 (순서)
1. **이 파일** (READ_FIRST.md)
2. `GOAL.md` — 프로젝트 north star(매일 아침 1회 호출 → 4단계 파이프라인)
3. `CHROME_SETUP.md` — Chrome for Testing/Profile 6/CDP 포트 규칙
4. **최신 `WORKLOG_YYYY-MM-DD.md`** (날짜 큰 것부터) — 가장 최근 결정·검증·미완·교훈
5. `LOTTE_HOMESHOPPING_STEPMAP.md` — 롯데홈쇼핑 폰결제 정본 스텝맵 (A~G)
6. `rate-check/RULES.md` — rate-check 절대룰(캐시금지/하드코딩금지 등)
7. `.claude/agents/project-manager.md` — 일일 5단계 오케스트레이션
8. **자동메모리** `/Users/jasonkim/.claude/projects/.../memory/MEMORY.md` + 관련 파일들
9. **관련 소스 정독** — 작업 영역 코드. 특히:
   - `phone_auto/hmall_hyundai_buy.py` — **멀티카드 결제 정본**: `detect_card`/`CARD_ALIASES`/`select_card`/`select_card_discount`/`_pick_card_from_grid`/`pay_hyundai|lotte|kb|hana|bc|samsung|nh`/`buy_one` 디스패처. `CARDS_SUPPORTED`=현대·롯데·KB·하나·BC·삼성·NH (7개 전부 라이브검증).
   - `phone_auto/lotte_homeshopping_buy.py` — 롯데홈쇼핑 폰 구매(A~G). 카드앱 구간은 위 SDK/카드 JSON 재사용.
   - `phone_auto/coords/apps/*.json` — 카드앱 flow(lotte_card/kb_kbpay/hana_card/bc_paybook_isp 등). 카드앱 PIN 구간은 **몰 무관 재사용**.

## 핵심 아키텍처 (흐름 요약 — 외워둘 것)
- **결제 = [몰-side: 결제하기 + 카드수단 선택] + [카드앱: PIN flow]**. 카드앱 구간은 어느 몰에서 호출하든 동일 → `*.json` flow / hmall `pay_X`의 카드앱 부분 재사용.
- **현대몰 주문서 포인트/카드선택 정본 = `flow_runner.py` `hmall_use_all_points` + `hmall_select_card_discount`** (식품·설화수 공통, 새로 만들지 말 것):
  맨위→700px → ① H.Point '전액사용' 탭(<100P 비활성/버튼없음=skip) → ② 캐러셀 '즉시할인' 행 금액 == 하단 '결제하기' 금액 **일치=선택됨(탭 금지)** / 불일치=탭→재확인 / 계속 불일치=**결제 차단**.
  당일카드 아닌 강제선택만 그리드(`_pick_card_from_grid`: 결제수단변경→신용카드 선택→페이/Pay→카드 탭). **'미등록' 개념 없음** — 모든 카드 항상 선택 가능.
- **토글 UI 원칙**: 현대몰 체크박스/카드할인 행은 토글 — **상태 확인 없이 무조건 탭 금지** (카트 전체선택=체크박스 픽셀색 R-G>60, 카드할인=금액일치로 판정).
- **당일 할인카드**는 매일 바뀜 → `detect_card()`로 주문서 '카드할인' 토큰 감지(`CARD_ALIASES`). 하드코딩 금지.
- **롯데홈쇼핑 비롯데 카드**: 롯데-side 카드수단 선택은 신규 관찰 필요하나, 카드앱 flow는 재사용. hmall `buy_one` 디스패처 패턴(detect_card→select_card→pay_X)을 그대로 이식.
- 뷰티포인트 적립 = 주문완료 화면 only(now-or-never), reward(구매사은 적립금)는 신청기간 내 재진입 가능. **뷰티가 reward보다 먼저+성공해야 함**.

## ★★ 롯데 장바구니 담기 — 비밀번호 변경 캠페인 (사용자 4회 지적, 2회 재발)

**로그인 직후 뜨는 비번변경 캠페인을 `30일간 보이지 않기` 로 닫고, 그 다음 본 창에서 담는다.**
안 닫고 담으면 **미로그인 컨텍스트라 실제로 담기지 않는다** (로그만 성공처럼 찍힘).

- 캠페인은 **별도 window** — `.../member/login/forward.popup_pwd_campaign_av.lotte`.
  넘겨받은 page 하나만 검사하면 못 찾고 **로그인 성공으로 오판**한다. → `context.pages` **전부** 스캔.
- 버튼에 **텍스트가 없다**. `<img alt="30일간 보이지 않기">` + 부모 `<a onclick="fn_changeNext()">`.
  `innerText`/`value` 로만 찾으면 절대 못 잡는다. → **`img[alt]` 매칭 필수.**
- 닫은 뒤 **창이 실제로 사라졌는지 검증**. 남아 있으면 `context.pages[-1]` 이 그 창을 잡아
  담기가 헛돌고(카트 0건), 창이 닫히는 순간 `Target page ... has been closed` 로 죽는다.
- ⚠️ **`지금 변경하기` 절대 클릭 금지** — 비밀번호가 실제로 바뀐다.
- 정본 = `buy/sulwhasoo.py` `_lotte_campaign_pages` / `_lotte_dismiss_pw_campaign` / `_lotte_pw_campaign_present`.
- 담기 후 **`카트 N건` 검증 로그 확인** 없이 "담기완료" 라고 보고하지 말 것.
- 피해 이력: 2026-07-21 1차 재발 / 2026-08-01 #2 카트 0건·#3 page-closed.

## 규칙
- 작업 지시 받으면 → 위 목록 읽기 → "흐름 이렇게 이해했다" 짧게 확인 → 작업. **읽기 전 코드 수정 금지.**
- 실결제(돈)·되돌리기 어려운 행동은 PIN 직전 금액 확인. 단 "N번~M번" 범위 지시는 중간 멈춤 없이 완주(자동메모리 lotte-range-run-no-pause).
- 추측 패치 금지 — 틀린 원인부터 파악(systematic-debugging). 실제 화면/코드 확인 후 수정.
- **페이지에서 뭘 읽을 땐 반드시 해당 영역으로 스코프를 좁힌다.** `document.querySelectorAll` 로
  전체를 긁으면 하단 **'최근 본 상품'·추천 캐러셀**이 섞여 들어온다 (2026-08-02: 현대몰 장바구니를
  그렇게 읽어 카트에 없는 상품을 '담겨있다'고 오독 → 사용자에게 잘못 보고). 장바구니는
  `rate-check/hmall.py cart_items()` 사용 — 카트 섹션(`div.shipping-listwrap`) 안에서만 읽는다.
- **관측이 도메인 상식과 모순되면 관측부터 의심한다.** "27조합 측정했는데 왜 무관한 상품이 있나"
  같은 모순은 그 자체가 내 읽기가 틀렸다는 신호다. 모순을 안은 채 보고하지 말 것.

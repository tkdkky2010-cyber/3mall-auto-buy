# Rate-Check 절대 룰 — 실수 방지 가이드

> **이 문서는 rate-check 모든 작업(코드 수정·실행·결과 검증)의 사전 체크리스트.**
> 매 step1 실행 전, 코드 수정 전, agent 작업 시작 전에 반드시 훑는다.
> **하드코딩된 값(특정 %, 원, 단가) 절대 포함하지 않는다** — 값은 매일 변경. 이 문서는 룰만 기술.

---

## §0. 메타 원칙 (위반 시 다른 모든 룰의 신뢰성 무너짐)

1. **사용자가 "확인하라"고 하면 실제 페이지에 들어가서 그 element 만 본다.** 출력값 / 캐시 / 보고서 / 이전 메시지로 판단 금지.
2. **사용자가 알려준 룰을 그대로 코드에 진짜 구현했는지 확인한다.** 룰을 들었다고 코드에 반영된 게 아님 — 실제 라인을 보고 verify.
3. **추측 / 휴리스틱 / "아마 이럴 것" 금지.** 모르면 실제 페이지를 fetch하고 DOM을 inspect한 후 fix한다.
4. **하드코딩된 예시값을 코드/문서에 넣지 않는다.** 매일 바뀔 수 있는 값(쿠폰%, 적립금, 추증가치, 단가, 임계값 등)을 "예: N%" 식으로라도 적으면 다음에 그 값이 재인용될 위험.
5. **"어제 같으니까" 가정 금지.** 모든 daily 값은 매일 fresh 확인. 캐시 / 옛 출력 / 옛 시트 데이터 따라쓰기 X.

---

## §1. 데이터 출처 (어디서 읽나)

### 1-1. 페이지 → 코드 추출
- **반드시 페이지의 가장 좁고 정확한 element 한 곳에서만 추출.** body 전체 / 페이지 전체 텍스트 regex 금지.
- 사용자가 명시한 element 가 있으면 그것만 사용. 다른 fallback X (있으면 옛/다른 영역의 stale 텍스트가 잡힐 수 있음).
- 사용자 명시 element 가 없으면 작업 전에 element 위치 확인 요청.

### 1-2. body 전체 검색 = 금지 (이유)
- 페이지에는 항상 옛 텍스트가 남아있다. 예시 (구조적):
  - Q&A 영역의 옛 고객 질문 (수년 전 작성된 텍스트)
  - 행사/배너 영역의 다른 카테고리 광고
  - 추천 상품 캐러셀의 다른 상품 정보
  - 푸터 / 약관 / 안내문의 일반 % 언급
- regex `\d+%` 만 잡으면 위 stale 텍스트가 같이 잡힘. `max()` 휴리스틱이면 stale 값이 진짜 값을 덮어쓸 수 있음.

### 1-3. 캐시 파일 read/write = 금지
- `*.json` (today_composition / hmall_results / _lotte_reward_dump 등) 결과파일 생성·읽기 절대 X.
- 데이터는 **Google Sheets** 가 single source of truth (SoT).
- 스크립트 간 데이터 전달은:
  1. sheet에 쓰고 다음 스크립트가 sheet에서 읽기 (선호)
  2. subprocess + stdout JSON 마커 (외부 스크립트 호출 시)
  3. in-process function call (orchestrator 패턴)
- 옛 결과파일이 디스크에 남아있을 가능성 항상 고려 — 매 실행 시작 시 가능한 삭제.

### 1-4. 시트 → 코드 읽기
- 스크립트가 다른 스크립트 결과를 참조해야 하면 sheet 셀에서 직접 파싱.
- 셀 위치는 동적일 수 있음 (행 N + max_samples 같은). 라벨 텍스트 검색으로 행 찾기.

---

## §2. 정렬 / 출력 순서

- **모든 결과는 조합번호 1~16 순서로 시트 입력.** 공급률 오름차순·내림차순 정렬 절대 X.
- `sorted(rows, key=lambda r: r["공급률"])` 같은 코드 발견 시 즉시 제거.
- 시트 컬럼 헤더에 "순위" 라는 단어가 있으면 잘못 — "조합번호" 로 표기.

---

## §3. 결과파일 잔존 0건 룰

Step 1 종료 후 `rate-check/_tmp/` 와 `rate-check/` 에 잔존해서는 안 되는 파일들:
- 추증/조합 계산 결과 JSON (galleria 출력 cache 등)
- 측정 결과 JSON (hmall_results 등)
- 적립 정보 dump JSON
- 미리보기 xlsx / csv

**예외 (잔존 허용)**: 사용자가 직접 작성한 input config 만. 예: GWP 구성 JSON (이미지 OCR 결과를 사람이 작성).

코드에서 결과파일 write 패턴 발견 시 (`.write_text`, `json.dump`, `with open ... 'w'`, `to_csv` 등) → 사용자 confirm 받기 전 commit 금지.

---

## §4. Hmall — 결제 페이지 실측 (수학 계산 금지)

- 옛 흐름 `정가 × 0.9 × (1 - 카드%)` 수학 계산은 **폐기**.
- 새 흐름: 장바구니 → 조합 체크/수량 조정 → 구매하기 → **결제 페이지 카드 캐러셀에서 카드별 즉시할인 금액 직접 측정**.
- 이유: 결제 페이지에 hidden 추가쿠폰이 적용될 수 있음 (장바구니에서는 보이지 않음).
- 페이백은 별도 — 실비 = (즉시할인후 × 페이백계수) − 적립금(즉시할인후 기준이 아닌 H 기준 — §5 참조).
- Chrome 9224 (rate-check 전용) 또는 9222 (Hmall main) 사용. 사용자가 9222를 다른 작업에 쓰는 중이면 9224 필수.

### Hmall cart 조작 DOM
- 체크박스: `input[name="backet"]` (name 속성 안정)
- 옵션변경 버튼: `<button>옵션변경</button>` (텍스트 매칭)
- 팝업 +/− 버튼: `<button>증가</button>` / `<button>감소</button>` (텍스트 매칭, hashed class 변동 가능)
- 변경하기: `<button class="change-btn">변경하기</button>` (class 우선, 폴백 텍스트)
- ⚠️ 변경하기 클릭 후 cart 리로드 → 인덱스/체크박스 리셋 → 수량 조정과 체크는 단계 분리, 매번 재 inspect.

### Hmall 결제 페이지 카드 캐러셀
- 카드 클릭: `div` exact text `"{카드명}{N}%즉시할인"` (예: 검색 패턴, 실제 텍스트 매일 다름)
- 클릭 후 가까운 section의 `<p><strong>` 또는 "총 결제금액" 영역에서 가격 읽기
- 카드별 1회씩 클릭 → wait → 가격 추출

### Hmall cart 상품명 매칭
- 카트 cell 첫 줄에 풀상품명 (예: `[본사직영]설화수 [공통]...`).
- 코드의 PRODUCTS 닉네임("윤조3종" 등)은 풀명과 다름 → CART_KEYWORDS 매핑 표 필요.
- 풀명에 포함되는 안정 키워드로 매칭. 키워드는 코드에 명시적 dict로 두되, 페이지 텍스트의 일부 substring 이어야 함.

---

## §5. check10 — H/I열 계산 (Step 2)

- **H = 즉시할인가** = 혜택가 × qty × (1 − 즉시할인%).
- **I = 실비** = round(H × (1 − 카드페이백%)) − 적립금**(H 기준)**.
- 적립금 계산 시 `_compute_reward()` 의 첫 인자는 **H** (즉시할인가). `after_payback` 이 들어가면 버그.
- 이유: 결제 시점 실제 결제 금액은 H. 페이백은 카드사가 별도 환급. 적립 정책은 결제 시점 금액 기준.

### check10 PRODUCTS의 alias_of
- 같은 URL 에 옵션이 여러 개인 경우 alias_of 로 base 와 연결.
- alias 의 옵션 가격은 **base 의 benefit_ratio (= 우수가/정가) × alias 옵션의 정가** 로 derive.
- 단순 base clone 금지 (옵션마다 가격 다름).
- 5만원 임계 미달 시 qty 자동 증가 (5만원 ≥ 만족할 때까지 +1).

---

## §6. 갤러리아

### 6-1. 쿠폰 추출
- **사용자가 명시한 element**: `button.down em` (또는 `button[onclick*="couponListLayer"] em`).
- 그 element 안의 텍스트만 사용. 페이지 전체 검색 / GOODS.info / body regex 전부 X.
- 첫 매칭 텍스트에서 `\d+\s*%` 추출.

### 6-2. 기본할인 추출
- `GOODS.info.price.sale_price` 대비 `cust_sale_price` 비율로 산출.
- 매일 변경 가능성 고려.

### 6-3. 네이버 구매할인
- 네이버 구매할인 2.2% 만 적용 (×0.978).
- 추가 차감 항목 절대 추가 금지 (×0.978 외의 곱셈 계수 추가 X).
- 카드 청구할인 없음 / 카드 페이백 없음.

### 6-4. 추가증정 샘플 / GWP
- 상품 페이지에서 당일 스크랩.
- 신규 품목 (단가표 미매칭) → 단가미정 표시, 해당 상품 공급률 계산 제외.
- GWP 1세트 구성은 이미지 OCR 후 사용자가 JSON 작성 (40/70만원 구매혜택).
- 단가표 (SAMPLE_TABLE) 는 별도 catalog (자주 안 바뀜) — daily 데이터 아님.

---

## §7. 롯데

### 7-1. 쿠폰 (per product)
- 상품 페이지 → "쿠폰받기" 클릭 → 팝업/레이어 등장 → **팝업 안의 가장 위 (다운로드 가능한 가장 높은 할인) 쿠폰 항목** 만 사용.
- 페이지 body 전체에서 `\d+%` regex max 금지 (배너 / 행사 표시가 같이 잡힘).
- 팝업 컨테이너 셀렉터 후보: `[class*="coupon"][class*="popup"]`, `[role="dialog"]` 등. 검색 시 visible (`offsetParent !== null`) + 텍스트에 "쿠폰" + `\d+%` 포함 필터.

### 7-2. 적립금 (per combo)
- **상품 페이지의 `#eventBanner` 슬라이드** 에서 적립 이벤트 후보 추출.
- 1차: 무시 키워드 list (가입 권유 / 일반 안내) 로 filter.
- 2차: data-url 페이지 진입 → 페이지 본문에서 적립 구간표 / 최대 한도 패턴 검증.
- 검증 패턴 (모든 매칭 잡기, global flag 필수):
  - `/([\d,]{3,})\s*원\s*이상[\s\S]{0,N}?([\d,]{2,})\s*(?:원|P)\s*적립/g` — 다행 구간표
  - `/최대\s*([\d,]+)\s*(?:원|P)\s*적립/g` — 단일 한도
- 구간이 여러 개면 **모두 잡아야 함** (regex lazy match가 중간 row 를 소비해서 다음 매칭을 놓치는 버그 주의 — 첫 매칭 후 lastIndex가 두 번째 "원 이상" 위치를 넘어가는 케이스).
  - Fix 방법: 모든 "원 이상" 위치와 모든 "원 적립" 위치를 따로 추출 후 순서대로 페어링. 또는 DOM 테이블의 `<tr>`/`<td>` 직접 순회.
- 각 이벤트의 `max_pt` = 이벤트 내 적립금 후보 중 **최대값**.
- 조합 합계가 임계 만족하는 가장 큰 구간 적립금을 선택 (조합당 1회만 적용).

### 7-3. 적립금 — 조합당 1회 (절대 룰)
- **`적립 = max(applicable rewards from combo) if applicable else 0`** — 조합 단위 1회 적용.
- **`sum(reward × qty)` 또는 `sum(reward for product) × qty` 절대 금지.** 결제 1회 = 이벤트 1회 발생.
- 임계가 있는 경우(예: "N원 이상 적립"): 조합 합계가 임계 만족하면 적립금 1회 차감, 미달이면 0.
- 여러 구간 있으면 조합 합계 만족하는 가장 높은 구간의 적립금 선택.

### 7-4. 카드 청구할인 / 페이백
- 카드 청구할인 (예: %p 즉시 차감) + 카드 페이백 (예: 청구일 환급) 별개.
- 페이백 대상 카드 매핑 dict는 catalog (안정값) — daily 아님.

---

## §8. 코드 = 마크다운 = 시트 동기화

- **워크트리에서 작업했으면 main 경로로 sync 필수.** agent 가 main 에서 실행하면 옛 코드 따라쓰는 버그 발생.
- sync 방향 주의: `cp worktree main` 이지 그 반대 X.
- `git diff -q main worktree` 가 0 이어야 작업 완료.

### Commit 룰
- 워크트리 브랜치(`claude/{name}`)에만 commit + push. main 으로 force push X.
- 사용자의 다른 main 작업 (buy/run.py 등) 건드리지 않음 — focused commit.
- 한 commit 에 너무 많은 변경 섞지 않음.

---

## §9. Chrome 인스턴스 분리

- Step 1 (rate-check) 전용 Chrome: **9224** (분리된 user data dir).
- Step 2 (check10): **9223**.
- buy/run.py 등 Hmall 메인 작업: **9222**.
- 사용자가 9222 를 다른 작업에 쓰는 중이면 step 1 은 9224 사용 필수 (cart/login state 충돌 방지).
- CDP attach 스크립트는 `chrome_launcher.ensure_chrome(port)` 호출.

---

## §10. 실수 패턴 카탈로그 (재발 방지)

각 패턴은 "원인 → 발생 영역 → 방지 방법" 형태.

### P1. body 전체 regex + max() 휴리스틱 → stale 텍스트 픽업
- 원인: 페이지에는 옛 Q&A, 행사 배너, 추천 상품 등 다른 % 텍스트가 항상 존재. body wide regex 가 이걸 다 잡고 max 취하면 진짜 값 덮어쓰기.
- 발생 영역: 갤러리아 쿠폰, 롯데 쿠폰, 어떤 페이지든.
- 방지: §1-2 (좁은 element 만), §6-1, §7-1.

### P2. 캐시 JSON 의존 → stale 값 재사용
- 원인: 어제/오늘 새벽 캐시를 재실행 시 그대로 읽음. 단가표 변경 / 쿠폰율 변동 미반영.
- 발생 영역: today_composition / hmall_results / _lotte_reward_dump.
- 방지: §1-3 (캐시 0건), §3 (잔존 0건).

### P3. 출력값을 곧이곧대로 수용 (verify 없음)
- 원인: 스크립트 출력이 N% 나 M원 나오면 "그게 맞을 것" 추정. 실제 페이지 확인 안 함.
- 발생 영역: 모든 단계.
- 방지: §0-1, §0-2. 사용자 보고 전 1상품 sample 페이지로 cross-check.

### P4. 룰을 코드에 진짜 구현했는지 검증 누락
- 원인: 사용자 룰 받음 → 코드에 한 줄 추가 → "OK" 판단. 그 한 줄이 실제로 룰을 실현하는지 verify 안 함.
- 예시: "구간별 max 적립" 룰 → 코드는 `max(tier_rows)` 까지만 했지만 tier_rows 가 1건만 잡힌 걸 못 봄.
- 방지: §0-2. fix 후 실제 페이지에서 결과 cross-check.

### P5. 워크트리에서만 수정 + main sync 누락
- 원인: 워크트리에 commit 만 하고 main 경로 cp 안 함. agent가 main에서 실행 시 옛 코드.
- 방지: §8. main = worktree diff 0 검증.

### P6. 정렬 코드 잔존
- 원인: 옛 정렬 코드 (rank_by_rate, sorted by 공급률) 가 코드/마크다운에 남아있음.
- 방지: §2. grep 으로 sorted, rank, 순위 패턴 sweep.

### P7. 적립 sum × qty
- 원인: per-product 적립 dict 가 있을 때 `sum(reward × qty)` 자연스러워 보이지만 잘못. 결제는 1회.
- 방지: §7-3.

### P8. lazy regex match → 두 번째 구간 누락
- 원인: `[\s\S]{0,N}?` lazy match 가 첫 매칭 후 lastIndex 이동 → 다음 "원 이상" 위치를 첫 매칭이 이미 소비했으면 못 잡음.
- 방지: §7-2 (모든 "원 이상"과 "원 적립" 따로 추출 후 페어링).

### P9. 옛값 / 예시값 코드·md 에 인용
- 원인: 코드 주석이나 문서에 "예: {%}", "예시: {임계금액}={적립금}" 같이 구체 숫자를 박아두면 다음 작업 시 그 값이 재사용·재인용됨 (사람도 agent도).
- 방지: §0-4. 추상 표기 (N%, M원, 임계값, 적립금 등 변수 이름) 만 사용. 어떤 daily 값도 문서에 박지 않음.

### P10. agent 위임 시 옛 코드 경로 미명시
- 원인: agent 에 "step1 진행해" 만 하면 main 경로 옛 코드 실행 가능. 워크트리 새 코드 미적용.
- 방지: agent 호출 시 main 경로 코드 상태 명시 + "옛 캐시 / 옛 결과 사용 X" 명시.

---

## §11. 매 실행 전 자가 검수 체크리스트

코드 수정 후 / step1 실행 전 다음 항목 모두 통과 확인:

- [ ] 코드/md grep: `0\.948 \| today_composition_*.json \| hmall_results.json \| _lotte_reward_dump.json \| load_today_composition \| rank_by_rate \| sorted.*공급률` — 0건
- [ ] 코드/md grep: `예:\s*\d+% \| 예\s*\)\s*\d+ \| 예시:?\s*\d` — 0건 (예시값 인용 금지)
- [ ] 코드/md grep: `sum\(rewards.*\*\s*q \| sum\(.*적립.*qty \| 적립\s*=\s*sum` — 0건
- [ ] main vs worktree diff: 0 (sync 완료)
- [ ] 모든 daily 데이터는 fresh scrape (sheet 또는 element 에서 직접). 캐시 dependency 0.
- [ ] 1상품 sample 로 cross-check (실제 페이지의 element 텍스트 vs 코드 출력)
- [ ] 결과파일 잔존 0건 (사용자 input config 제외)
- [ ] 시트 결과 row 순서 = 조합번호 1~16

---

## §12. evaluator 역할 (선택사항 — 자동화 시)

이 파일을 evaluator agent 의 input 으로 사용 가능. 다음을 자동화:
1. 알려진 stale 패턴 grep (§11 체크리스트)
2. 1상품 sample fetch → 코드 결과와 cross-check
3. main = worktree sync 검증
4. 결과파일 0건 검증

evaluator 발견 시 사용자 confirm 받고 fix. 자동 fix 금지 (root cause 못 잡고 패치만 할 위험).

---

## §13. 시트 "{M.DD}" 탭 layout 구조 — 16조합 (2026-05-16 확정)

**구조만 기술 — 셀 값(공급률, 가격 등)은 매일 다르므로 박지 않는다.**

### 13-1. 행 영역 (vertical layout)
- **행 1~46**: 갤러리아 섹션 (제목 / 할인정보 / GWP 구성 / 추증가치 / 공급률 요약)
- **행 47~48**: 빈 (간격)
- **행 49~70**: 현대Hmall 섹션 (제목 / 카드정보 / 16조합 공급률 요약, 22행)
- **행 71~72**: 빈
- **행 73~95**: 롯데 섹션 (제목 / 쿠폰/카드/적립 정보 / 16조합 공급률 요약 + 우측 쿠폰 블록, 23행)
- **행 1~19, O~R열**: 카트플랜 출력 (cart_plan.py — galleria K~M 비교차트와 분리, J까지만 사용)

→ 각 mall 스크립트의 `START` 변수:
- galleria.py: row 1부터 (write_grid start_row=1, batch_clear A1:I48)
- hmall.py: `START = 49` (write_grid start_row=49)
- lotte.py: `START = 73`

### 13-2. 3사 공급률 비교 차트 — J1:M17
오른쪽 상단에 배치 (Galleria 섹션과 같은 행 영역 1~17, 갤러리아 데이터 안 닿는 J~M열).

| 열 | 행 1 (header) | 행 2~17 (조합 1~16) |
|---|---|---|
| J | "조합" | 조합번호 (1~16) |
| K | "갤러리아몰" | 갤러리아 공급률 |
| L | "Hmall" | Hmall 공급률 |
| M | "롯데" | 롯데 공급률 |

→ 각 mall 스크립트가 자기 컬럼만 채움:
- galleria.py: J1:K17 (조합번호 + 갤러리아 공급률)
- hmall.py: L1:L17 (Hmall 공급률)
- lotte.py: M1:M17 (롯데 공급률) + 조건부 서식 추가

### 13-3. 조건부 서식 — K2:M17 행별 최저값 강조
- **범위**: K2:M17 (3사 × 16조합)
- **조건**: 셀 값이 같은 행의 K~M 최솟값과 같으면 (CUSTOM_FORMULA)
- **포맷**: 연두색 배경 (RGB ~ 0.72, 0.92, 0.72)
- **의미**: 각 조합에서 3사 중 가장 좋은 (낮은) 공급률 강조 — 한눈에 베스트 몰 식별

→ lotte.py 끝에서 `sh.batch_update` 로 `addConditionalFormatRule` 호출. 중복 추가되어도 동작은 동일하지만, 정기적으로 sh의 기존 rule 정리 권장.

### 13-4. 카트플랜 출력 — O1:R19
cart_plan.py 가 작성. 자동 채널 선택 + N개 분배 결과 (Step 1 끝 자동 실행).
- O1: "카트 플랜" / P1: 채널 / Q1: f"N={n}" / R1: 날짜 (M.DD)
- O2:R2: 헤더 ["조합번호", "조합", "구매수", "공급률"]
- O3~ : 데이터 행 (조합번호 순 정렬, x>0 만)
- 마지막+1 행: 합계 ["{channel}", "총 회수", "총 결제", "평균 공급률"]

### 13-5. 구조 변경 가능성
- 사용자가 행 간격을 더 줄이거나 늘릴 수 있음. 변경 시 각 mall 스크립트의 `START` 와 비교 차트 row 범위 update.
- 비교 차트 위치 (J~M) 도 변경 가능. 갤러리아 main 데이터(A~G)와 우측 쿠폰 블록(I~K)이 K열까지 사용하므로 J 시작은 K와 살짝 겹침. 사용자 layout 기준으로 J="조합" 으로 사용.
- ⚠️ Galleria의 우측 쿠폰 블록(I~K)과 비교 차트(J~M, 행 1~17)는 행 영역이 다르므로 충돌 없음. 단 갤러리아 K1 ("갤러리아몰" 비교 차트 헤더) vs Galleria 섹션 K행은 분리.

---

## §14. 이 문서 자체 룰

- **이 문서를 100줄 넘어도 단일 파일로 유지.** 여러 파일 분산 X.
- **하드코딩된 값을 이 문서에 절대 추가하지 않는다.** 새 룰 추가 시도 추상 표기로.
- 새 실수 패턴 발견 시 §10 에 추가.
- 사용자가 새 element / 새 룰 알려주면 해당 섹션에 추가 + 옛 patterns 제거.

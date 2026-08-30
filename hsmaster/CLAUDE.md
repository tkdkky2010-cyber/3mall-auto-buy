# hsmaster — CLAUDE.md

> **상속**: 이 파일은 `../CLAUDE.md` (행동 가이드라인)을 상속한다. 충돌 시 본 파일이 우선.
>
> 🔒 **PRIVATE 100%, 외부 공개 절대 금지.**
> README, NOTICE, LICENSE, 공개 문서 일체 만들지 말 것. 커밋 메시지에도 비밀번호·계정 ID·내부 URL을 적지 말 것.

---

## 프로젝트 개요

홈쇼핑 차익거래 자동화 CLI. 두 개의 독립적인 트랙.

### Track A — 설화수 자동구매 (3몰)
- 갤러리아몰 / 현대H몰 / 롯데홈쇼핑 3사에 **같은 SKU 7개(b~h)** 존재.
- **고정 11개 조합** — 본품 구성은 안 바뀜 (`core/sulwhasoo/combos.ts` 하드코딩).
- 외부 시스템(gspread)이 매일 3몰 공급률 시트에 분석 결과 입력. 사용자가 시트 보고 결정.
- CLI: `hsm sulwhasoo cart --combo N --mall <hyundai|lotte|galleria> --account M`
  - 해당 몰 격리 컨텍스트 로그인 → 장바구니 비움 → 11개 조합표대로 담기 → 쿠폰받기 → 카트 출력
- 적립 계산 X, 조합 최적화 X.

### Track B — Hmall 우수스토어 적립 자동구매 (현대H몰만)
- 건강식품 16~29개 상품 중 "10% 단순적립"만 매일 변동 (`Hmall_10__Check_Guide.md` 참고).
- 19계정으로 구간별 적립 최대화. 카드할인 + 쿠폰 + 적립 곱셈 최적화.
- **Phase 2 이후.** 지금은 미구현.

두 트랙 모두 결제 마지막의 카드 7자리·비밀번호를 **코드가 폰에 자동입력**한다 (Phase 3).
셔플 키패드는 에이전트가 좌표만 판독해 넘기고, 값은 스크립트가 secrets 에서 읽는다. 사람이 입력하지 않는다.

---

## 19계정 풀 (Track A·B 공유)

- 파일: `~/Desktop/Vibe Coding/Hmall10/vipmall.json`
- 포맷: `{ "accounts": [ { "id": "...", "pw": "..." }, ... ] }` — 19개 배열.
- `--account N` = 1-indexed (1~19). `--account` 미지정 시 오늘 한도 안 찬 가용 계정 자동 선택.

### 일일 사용 한도 (KST 기준, 자정 리셋)

| 트랙 | 갤러리아 | 현대 | 롯데 |
|---|---|---|---|
| `sulwhasoo` (Track A) | 3 | 3 | 1 |
| `hmall_reward` (Track B) | — | 2 | — |

- 정의: `src/core/limits.ts` `DAILY_LIMITS` 상수.
- 카운트는 `data/state.db` `account_usage` 테이블에 기록. **장바구니 담기 성공 시점에 +1.** 결제 카운트는 buy.ts 단계에서 재정의.

---

## 디렉토리 구조

```
hsmaster/
├── bin/hsm                         # CLI 엔트리 (tsx로 src/cli/index.ts 실행)
├── src/
│   ├── cli/
│   │   ├── index.ts                # commander 라우팅
│   │   └── commands/
│   │       ├── status.ts
│   │       └── sulwhasoo/
│   │           ├── cart.ts         # Phase 1
│   │           └── buy.ts          # Phase 2
│   ├── malls/
│   │   ├── base.ts                 # Mall 인터페이스, Account 타입, 계정 로더
│   │   ├── hyundai.ts              # Phase 1
│   │   ├── lotte.ts                # Phase 2
│   │   └── galleria.ts             # Phase 2
│   ├── core/
│   │   ├── limits.ts               # DAILY_LIMITS + 자동선택 로직
│   │   ├── kst.ts                  # KST 날짜 유틸
│   │   └── sulwhasoo/
│   │       ├── products.ts         # 7개 정적 메타 (b~h)
│   │       ├── combos.ts           # 11개 본품 조합
│   │       └── ids.ts              # config/sulwhasoo-ids.json 로드/갱신
│   └── db/
│       └── state.ts                # better-sqlite3 + account_usage
├── config/
│   └── sulwhasoo-ids.json          # 동적 몰별 상품 ID (gitignore)
├── data/
│   └── state.db                    # SQLite (gitignore)
├── package.json
├── tsconfig.json
└── CLAUDE.md
```

---

## 자동화 규칙 (Hmall_10 가이드 부록 기반)

- **격리 컨텍스트 필수**: `chromium.launch()` 후 `browser.newContext()`로 매 계정 새 컨텍스트. 작업 종료 시 `context.close()`로 흔적 통째 폐기.
- 페이지 로딩 대기 **최대 3초**, 5초 이상 wait 금지.
- 로그인 실패 = 자동화 차단 → 새 컨텍스트로 재시도 1회.
- "구매하기" 버튼은 **정확히 한 번만** 클릭. 이중 클릭 금지.
- Hmall 옵션 분기: `button.btn-purchase` 클릭 후 1~2초 대기 → `span.choice-num.title` 존재 여부로 다중/단일 판별.
- **본문 하단 `btn-cart` 절대 사용 금지** (연관/추천 상품용).

---

## 상품 ID 만료 감지 + 갱신

설화수 7개 SKU의 **몰별 시스템 ID는 월 1회 갱신**된다. 하드코딩만으론 부족.

- 정적 메타(이름·소비자가)는 `core/sulwhasoo/products.ts`.
- 동적 ID(galleria_goods_no / hyundai_slitmCd / lotte_searchKey)는 `config/sulwhasoo-ids.json`.
- `addToCart` 실행 중 다음이 감지되면 **즉시 중단**하고 사용자에게 새 ID 요청:
  - URL 이동 후 404 / "상품을 찾을 수 없습니다" / 메인 리다이렉트
  - 페이지 제목·상품명이 `PRODUCTS[code].name`과 명백히 불일치
  - "구매하기" 버튼 미존재
- 사용자 입력 받으면 `sulwhasoo-ids.json` 자동 갱신 + `lastUpdated` 갱신.
- 매 cart 실행 시작 전 `lastUpdated`가 30일 경과면 경고 출력.

---

## 명령어 (현재까지)

```bash
hsm sulwhasoo cart --combo <1~11> --mall <hyundai|lotte|galleria> [--account <1~19>] [--dry-run]
hsm status
```

Phase 1에서 `--mall lotte`/`--mall galleria`는 미구현 (호출 시 명시적 에러).

---

## 작업 원칙 재확인 (../CLAUDE.md 강조점)

1. **Think Before Coding** — 가정 명시. 다중 해석은 사용자에 묻기.
2. **Simplicity First** — 단일 사용 추상화 금지. 200→50라인 가능하면 다시 짜라.
3. **Surgical Changes** — 해당 변경만. 인접 코드 "개선" 금지.
4. **Goal-Driven** — 매 단계 verify 가능한 체크 포인트.

특히 **이 프로젝트는 PRIVATE이므로**: 외부 노출용 문서·README·LICENSE 만들지 말 것.

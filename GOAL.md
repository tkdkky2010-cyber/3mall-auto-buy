# 최종 목표

매일 아침 에이전트에게 한 마디 ("오늘 할 거 해줘") → 4단계 파이프라인 자동 실행.
사용자 개입은 **Step 3 자연어 cart plan 입력 1회만**.

## 흐름

```
[USER]  "오늘 할 거 해줘"
   ↓
[AGENT: project-manager]
   ├─ Step 1: 설화수 3몰 공급률 자동 분석    (rate-check/run.py)
   │           → 갤러리아/Hmall/롯데 11개 조합 가격 → Google Sheets
   ├─ Step 2: Hmall 10% 적립 상품 자동 필터  (cart/check10.py)
   │           → 우수스토어 16~29개 상품 중 단순 10% 적립만
   │           → 사용자에게 리스트 표시
   ├─ Step 3: cart plan 자연어 입력           ← USER 개입 1회
   │           "9번 5계정 2개씩, 17번 3계정 1개씩" 같은 형식
   │           → buy/cart_plan.json 변환
   └─ Step 4: 19계정 cart 담기 + 결제          (buy/run.py)
               → 카드 결제창 7자리 코드 추출
               → 폰 ADB로 카드 앱에 자동 입력 (Phase 3-B)
               → 카드 PIN 6자리 자동 입력 (Phase 3-B)
   ↓
[DONE]  19계정 결제 완료
```

## 현재 상태 (2026-05)

| 모듈 | 상태 | 비고 |
|---|---|---|
| `.claude/agents/project-manager.md` | ✅ 완성 | 4단계 호출 구조 |
| `buy/run.py` | ✅ Phase 3-A 완성 | cart→checkout→7자리 추출. 캐러셀 자동 판독 + Playwright real-click + NH 7자리 (2-2-3) 지원 |
| `rate-check/run.py` | 🚧 미구현 | 가이드: `rate-check/Sulwhasoo_Supply_Rate.md` 1700+줄 |
| `cart/check10.py` | 🚧 미구현 | 가이드: `cart/Hmall 10% Check Guide.md` |
| `buy/lotte.py` / `buy/galleria.py` | 🚧 미구현 | hsmaster TS는 cart 담기까지만 |
| Phase 3-B (폰 자동화) | 🚧 폰 도착 후 | ADB + Tasker, 7자리 + PIN 자동 입력 |

## 작업 우선순위

1. **Step 1 자동화** (`rate-check/run.py`)
2. **Step 2 자동화** (`cart/check10.py`)
3. 19계정 batch 시범 운영 (`buy/run.py`)
4. 롯데/갤러리아 결제 모듈
5. Phase 3-B 폰 자동화

## 원칙

- **PRIVATE 100%** — 외부 공개 절대 금지 (이 레포는 단독 사용·비공개).
  - **노출 금지 = 인증정보 한정**: 비밀번호·카드번호·CVC·PIN·인증서·세션. 코드·커밋·커밋메시지 어디에도 평문 금지 (`.gitignore` 가 `hmall_config.json`·`lotte.json`·`galleria.json`·`credentials.json`·`secrets/`·`card_pins.json` 차단).
  - **계정 ID(식별자)는 허용**: 작업 기록·검증 결과·1회용 정리 스크립트에 계정 ID 남겨도 됨 (단독 사용자가 자기 기록을 읽으려면 필요). → 계정 ID 박혀 있다는 이유만으로 파일 삭제/커밋제외 판단 금지.
- **로그인 세션 유지** — `~/HmallChrome` 영구 프로필 + Chrome for Testing (메인 Chrome 분리)
- **DRY_PAYMENT=true** 안전장치 — 7자리 추출까지만, 실 결제 X (Phase 3-B 완성 전)

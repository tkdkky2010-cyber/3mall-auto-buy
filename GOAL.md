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

- **PRIVATE 100%** — 외부 공개 절대 금지 (커밋 메시지에도 비밀번호/계정 노출 X)
- **로그인 세션 유지** — `~/HmallChrome` 영구 프로필 + Chrome for Testing (메인 Chrome 분리)
- **DRY_PAYMENT=true** 안전장치 — 7자리 추출까지만, 실 결제 X (Phase 3-B 완성 전)

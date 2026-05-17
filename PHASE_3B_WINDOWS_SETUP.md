# Phase 3-B Windows 셋업 가이드

**목표**: Mac 에서 lotte 카트 fill 완료된 7계정에 대해, Windows 머신이 ESP32 + 웹캠 + 폰을 조작해 자동 결제 + 적립 받기까지.

**이 문서는 Windows 에서 새 Claude Code 세션을 시작할 때 읽는 진입점.**

---

## 시스템 컨텍스트

```
Mac (Layer 1, 이미 완성):
  rate-check → cart_plan → buy/sulwhasoo.py lotte cart fill (7계정 × 1조합)
  → lotte 카트에 상품 담긴 상태로 종료 (LOTTE_CART_ONLY=true)
  → 시트 5/17 탭 O~U 영역에 cart_plan 결과 기록

Windows (Phase 3-B, 이번 작업):
  ESP32-S3 (USB HID) ↔ 폰 (안드로이드, lotte 앱)
  로지텍 웹캠 → PC → OCR 으로 폰 화면 인식
  PC → HTTP REST → ESP32 → 폰 클릭/타이핑
```

---

## 핵심 결정사항 (이미 확정)

| 항목 | 결정 |
|---|---|
| lotte 결제 자동화 방식 | 폰 앱 (적립 받으려면 앱 결제 필수) |
| galleria 결제 | 컴터에서 자동 (`buy/sulwhasoo.py galleria_checkout`) — 변경 X |
| hmall 결제 | 컴터에서 자동 (`buy/run.py --checkout`) — 변경 X |
| 폰 OS | Android |
| 화면 동기화 | 로지텍 웹캠 + OCR |
| 보안 키패드 비번 | OCR 으로 자릿수 위치 추출 → ESP32 클릭 |
| 7계정 분배 | 7계정 × 1조합 각각 (적립 7회 받기) |
| 계정-조합 매핑 | cart_plan 추천 순서 (계정 1=조합 13 고정, 2~7=2/5/10/17/18/20) |

---

## 필요 디바이스 + 케이블

- [ ] ESP32-S3 dev board (`esp32_firmware/main/main.ino` 펌웨어 사용)
- [ ] Android 폰 (lotte 앱 설치 + 7개 계정 로그인 가능)
- [ ] USB-C OTG 케이블 (ESP32 ↔ 폰)
- [ ] USB-A ↔ USB-C 케이블 (PC ↔ ESP32 펌웨어 upload)
- [ ] 로지텍 웹캠 (USB) + 폰 화면 향하는 거치대
- [ ] 폰 전원 (계속 켜진 상태, 또는 ESP32 OTG 5V 공급)

---

## Windows 환경 셋업

### 1. 기본 도구
```powershell
# Python 3.10+
winget install Python.Python.3.12
# 또는 python.org installer

# Git
winget install Git.Git
# 또는 git-scm.com

# Tesseract OCR
winget install UB-Mannheim.TesseractOCR
# 또는 https://github.com/UB-Mannheim/tesseract/wiki
# 설치 후 PATH 추가: C:\Program Files\Tesseract-OCR\
```

### 2. Tesseract 한글 데이터 확인
```powershell
tesseract --list-langs
# 'kor' 가 보여야 함. 안 보이면 설치 시 "Korean" 언어 선택 빠짐 →
# https://github.com/tesseract-ocr/tessdata 에서 kor.traineddata 받아
# C:\Program Files\Tesseract-OCR\tessdata\ 에 넣기
```

### 3. Python 패키지
```powershell
pip install opencv-python pytesseract requests pillow numpy
# 옵션 (Tesseract 보다 한글 인식 더 강함 — 무겁지만):
pip install easyocr  # GPU 있으면 cuda toolkit 도
```

### 4. Repo clone + 보안 파일 이식
```powershell
cd C:\Users\<you>\Desktop
git clone https://github.com/tkdkky2010-cyber/3mall-auto-buy.git
cd 3mall-auto-buy
```

**보안 파일 (gitignored, repo 에 없음)** — Mac 에서 USB 드라이브 또는 secure transfer 로 옮기기:
- `lotte.json` (계정 7개 이상)
- `lotte_address_map.json` (계정별 dlvp_sn 매핑)
- `credentials.json` (네이버페이 등)
- `hmall_config.json` (hmall 식품 결제용 — Phase 3-B 직접 사용 X 지만 일관성)
- `galleria.json`
- `gen-lang-client-0553550811-4b553902b0d0.json` (gspread service account)
- `buy/.env`
- `hsmaster/config/sulwhasoo-ids.json`

→ Mac 에 있던 위 파일을 USB 로 그대로 Windows repo 같은 경로에 복사.

### 5. ESP32 펌웨어 upload
`esp32_firmware/README.md` 참고. Arduino IDE 2.x + ESP32 보드 매니저. `WIFI_SSID` / `WIFI_PASS` 본인 wifi 로 수정 후 upload. Serial Monitor 로 IP 확인.

### 6. 폰 준비
- lotte 앱 설치 + 7계정 미리 로그인 검증 (자동화 시 계정 전환 흐름 필요)
- 화면 잠금 시간 30분+ (자동화 중 꺼지면 안 됨)
- 화면 밝기 자동 → 수동 최대 (OCR 안정성)

---

## Phase 3-B 단계별 작업 (Windows 세션에서)

### α: 인프라 검증 (1시간)
- [ ] ESP32 wifi 연결 + IP 확인 (`curl http://<ESP32_IP>/status`)
- [ ] 폰에 ESP32 USB OTG 연결 → `/click {"x":540,"y":1200}` 호출 → 폰 화면 임의 좌표 클릭 동작 확인
- [ ] 로지텍 웹캠 OpenCV 인식 → 1프레임 캡처 + PNG 저장
- [ ] Tesseract OCR 으로 한글 텍스트 추출 검증 (lotte 앱 로고 화면 캡처 → "롯데" 추출)

### β: lotte 앱 1화면 자동화
- [ ] lotte 앱 메인 → 장바구니 진입 (좌표 매핑 + ESP32 click)
- [ ] OCR 으로 장바구니 화면 도달 확인 (예: "장바구니" 헤더 텍스트)
- [ ] "주문하기" 버튼 클릭 → 다음 화면 OCR 확인

### γ: 전체 결제 흐름 (1계정 dry-run)
- [ ] 주소 선택
- [ ] 쿠폰 적용 (직접/플러스 쿠폰)
- [ ] L포인트 사용
- [ ] 카드사 선택 (당일 청구할인 카드 자동 감지 — Mac `lotte.py` 와 동일 로직)
- [ ] 결제하기 버튼 → 카드 비번 입력 단계 진입까지

### δ: 보안 키패드 OCR + 비번 자동 입력
- [ ] 카드 비번 키패드 화면 캡처
- [ ] 키패드 각 셀 OCR → 숫자 위치 매핑
- [ ] 비번 자릿수별 해당 셀 좌표 → ESP32 `/click` 순차 호출
- [ ] 결제 완료 화면 OCR 검증

### ε: 7계정 sequential + PM 통합
- [ ] 계정 전환 (lotte 앱 로그아웃 → 다음 계정 로그인) 자동화
- [ ] cart_plan 결과 (시트 또는 stdout JSON) 읽어 채널/조합/계정 매핑
- [ ] 7계정 순차 결제 + 결제 완료 검증
- [ ] PM agent `.md` 에 substep #8 추가 (Phase 3-B 실행)

---

## 신규 코드 위치 (제안)

```
3mall-auto-buy/
├── phone_auto/                ← 신규
│   ├── esp32_client.py        # ESP32 REST API 클라이언트 (/click, /tap, /type)
│   ├── camera.py              # OpenCV 웹캠 캡처
│   ├── ocr.py                 # Tesseract / EasyOCR 래퍼
│   ├── lotte_app.py           # lotte 앱 화면 인식 + 액션 시퀀스
│   ├── secure_keypad.py       # 보안 키패드 OCR + 비번 입력
│   ├── coordinates.json       # lotte 앱 화면별 좌표/텍스트 anchor
│   └── run_lotte_payment.py   # CLI: 1계정 결제 (Mac cart fill 후 호출)
└── esp32_firmware/            # 이미 있음 (펌웨어 코드)
```

---

## 주의사항

- ESP32 USB OTG 로 폰 연결 시 폰이 ESP32 에 5V 공급 → 폰 배터리 소모. 결제 중 충전 케이블 분리.
- lotte 앱 UI 가 업데이트로 좌표 변경 가능 → OCR 기반 anchor (텍스트 위치) 가 좌표 fixed 보다 robust.
- 7계정 sequential 시 lotte 측 비정상 활동 감지 가능성 → 계정 간 5초+ 간격, 동일 IP/디바이스 우려 시 검토.
- 카드 비번을 PC 에 저장하면 보안 위험. `lotte.json` 처럼 별도 `card_pins.json` (gitignored) 으로 관리. 또는 매 실행 시 사용자 한 번 입력.

---

## Mac ↔ Windows 데이터 흐름

```
[Mac]                              [Windows]
─────────                          ─────────
rate-check + cart_plan
buy/sulwhasoo.py lotte cart fill
  → lotte 7계정 카트 채워짐
  → 시트 5/17 탭 O~U 결과 기록
                                   git pull (코드 sync)
                                   phone_auto/run_lotte_payment.py
                                     ↓
                                   시트 또는 stdout 읽음
                                     ↓
                                   채널/조합/계정 매핑
                                     ↓
                                   ESP32 + 폰 + 웹캠 → 7결제 자동
                                     ↓
                                   결제 완료 → 시트 X열 status 갱신
```

---

## 다음 세션 첫 명령 (Windows 에서)

```powershell
# Claude Code 시작
cd C:\Users\<you>\Desktop\3mall-auto-buy
claude
```

세션 첫 입력으로 이 문서 참조:
> "PHASE_3B_WINDOWS_SETUP.md 읽고 Phase α 부터 시작해줘. ESP32 IP 는 <IP>"

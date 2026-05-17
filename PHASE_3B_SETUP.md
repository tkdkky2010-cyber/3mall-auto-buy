# Phase 3-B 셋업 가이드 (Mac 단독 운영)

**목표**: 매일 새벽 또는 사용자 명령으로 Mac 하나가 rate-check → cart fill → lotte 7계정 자동 결제 (ESP32 + 웹캠 + 폰) 까지 끝.

**운영 방식**: macOS Spaces (Ctrl+Up) 데스크탑2 에서 자동 구매 실행 + 사용자는 데스크탑1 에서 일반 작업. 또는 새벽 cron 으로 자는 동안 자동 처리.

---

## 시스템 컨텍스트

```
Mac (전체 운영)
├── 데스크탑1: 사용자 일반 작업 (브라우저, 코딩, etc.)
└── 데스크탑2: 자동 구매 실행
     ├── rate-check (galleria/inventory/hmall/lotte) → 시트 입력
     ├── cart_plan → 채널 + 7계정 매핑
     ├── buy/sulwhasoo.py lotte cart fill (7계정)
     └── phone_auto/run_lotte_payment.py
          ├── ESP32 (USB HID) ↔ Android 폰 (lotte 앱)
          ├── 로지텍 웹캠 → OpenCV 캡처
          └── Tesseract OCR → ESP32 클릭/타이핑 명령
```

---

## 확정 결정사항

| 항목 | 결정 |
|---|---|
| lotte 결제 방식 | 폰 앱 자동화 (적립 받으려면 앱 결제 필수) |
| galleria 결제 | 컴터 (`buy/sulwhasoo.py galleria_checkout`) — 변경 X |
| hmall 결제 | 컴터 (`buy/run.py --checkout`) — 변경 X |
| 폰 OS | Android |
| 화면 동기화 | 로지텍 웹캠 + OCR (ADB/scrcpy 안 씀 — lotte 앱 보안 검출 우회) |
| 보안 키패드 비번 | OCR 으로 자릿수 위치 추출 → ESP32 클릭 |
| 7계정 분배 | 7계정 × 1조합 각각 (적립 7회) |
| 계정-조합 매핑 | cart_plan 추천 순서 (계정 1=고정조합, 2~7=cart_plan 추천 순) |

---

## 필요 디바이스

- [ ] ESP32-S3 dev board (`esp32_firmware/main/main.ino` 펌웨어)
- [ ] Android 폰 (lotte 앱 + 7계정 로그인 가능)
- [ ] USB-C OTG 케이블 (ESP32 ↔ 폰)
- [ ] USB-A ↔ USB-C 케이블 (Mac ↔ ESP32 펌웨어 업로드 시만)
- [ ] 로지텍 웹캠 (USB) + 폰 화면 향하는 거치대
- [ ] 폰 거치대 (안정적 위치, 진동/움직임 없게)

---

## 환경 셋업 (Mac)

### 이미 있음 ✓
- Tesseract OCR 5.5.2 + 한글 데이터 (`kor`, `eng`)
- pytesseract
- repo (이 디렉토리)
- 모든 보안 파일 (`lotte.json`, `lotte_address_map.json`, `gen-lang-*.json`, etc.)

### 설치 필요
```bash
pip install opencv-python pillow numpy
# 옵션 (Tesseract 보다 한글 인식 강함, 무거움):
pip install easyocr
```

### ESP32 펌웨어 업로드 (Arduino IDE)
1. Arduino IDE 2.x + ESP32 보드 매니저 ([esp32_firmware/README.md](esp32_firmware/README.md) 참고)
2. `esp32_firmware/main/main.ino` 열고 `WIFI_SSID` / `WIFI_PASS` 본인 wifi 로 수정
3. Mac USB-C 로 ESP32 연결 → Upload
4. Serial Monitor 로 ESP32 IP 확인 (예: `192.168.0.123`)
5. Mac USB 분리 → ESP32 USB-C OTG 케이블로 폰에 연결 (폰이 5V 공급, ESP32 가 폰의 HID 입력장치)

### 웹캠 셋업
- 폰을 거치대에 고정 (lotte 앱 전체 화면이 웹캠 시야에 들어오게)
- Mac USB 에 웹캠 연결
- macOS 카메라 권한: 시스템 설정 → 개인정보 → 카메라 → Terminal / Python 허용

---

## 운영 모드 (둘 다 가능, 동시 사용 OK)

### (A) 실시간 모드 — 사용자 명령
1. macOS Mission Control (Ctrl+Up) → 새 데스크탑 생성 (데스크탑2)
2. 데스크탑2 에서 Terminal 열고:
   ```bash
   python3 phone_auto/run_lotte_payment.py
   ```
3. 사용자는 데스크탑1 에서 일반 작업 (Ctrl+Left 로 데스크탑1 이동)
4. 진행 상황 확인 필요시 데스크탑2 로 이동 (Ctrl+Right)
5. ⚠️ 자동 구매 중 **웹캠 사용하는 앱 (FaceTime/Zoom/카메라) 안 켜기**

### (B) 새벽 cron 모드 — 자동
1. macOS pmset 으로 자동 wake 설정:
   ```bash
   sudo pmset repeat wakeorpoweron MTWRFSU 03:50:00
   ```
2. crontab 또는 launchd 로 매일 새벽 4시 실행:
   ```bash
   crontab -e
   # 추가:
   0 4 * * * cd "/Users/jasonkim/Desktop/Vibe Coding/3mall auto buy" && /usr/bin/python3 phone_auto/run_daily.py >> logs/cron-$(date +\%Y-\%m-\%d).log 2>&1
   ```
3. `phone_auto/run_daily.py`: rate-check + cart_plan + cart fill + lotte 결제 전체 자동
4. 사용자 깨면 결제 완료, 시트 확인

---

## Phase 3-B 단계별 작업

### α: 인프라 검증 (~1시간)
- [ ] ESP32 wifi 연결 + IP 확인 (`curl http://<ESP32_IP>/status`)
- [ ] 폰에 ESP32 USB OTG 연결 → `/click {"x":540,"y":1200}` → 폰 화면 임의 좌표 클릭 동작 확인
- [ ] 로지텍 웹캠 OpenCV 인식 → 1프레임 캡처 + PNG 저장
- [ ] Tesseract OCR 한글 텍스트 추출 검증 (lotte 앱 로고 캡처 → "롯데" 추출)

### β: lotte 앱 1화면 자동화
- [ ] lotte 앱 메인 → 장바구니 진입 (좌표 + ESP32 click)
- [ ] OCR 으로 장바구니 화면 도달 확인 ("장바구니" 헤더 텍스트)
- [ ] "주문하기" 버튼 클릭 → 다음 화면 OCR 확인

### γ: 전체 결제 흐름 (1계정 dry-run)
- [ ] 주소 선택
- [ ] 쿠폰 적용 (직접/플러스 쿠폰)
- [ ] L포인트 사용
- [ ] 카드사 선택 (당일 청구할인 카드 자동 감지 — Mac `lotte.py` 와 동일 로직 활용)
- [ ] 결제하기 버튼 → 카드 비번 입력 단계 진입까지

### δ: 보안 키패드 OCR + 비번 자동 입력
- [ ] 카드 비번 키패드 화면 캡처
- [ ] 키패드 각 셀 OCR → 숫자 위치 매핑
- [ ] 비번 자릿수별 해당 셀 좌표 → ESP32 `/click` 순차 호출
- [ ] 결제 완료 화면 OCR 검증

### ε: 7계정 sequential + PM 통합
- [ ] 계정 전환 (lotte 앱 로그아웃 → 다음 계정 로그인) 자동화
- [ ] cart_plan 결과 (시트 또는 stdout JSON) 읽어 매핑
- [ ] 7계정 순차 결제 + 결제 완료 검증
- [ ] PM agent `.md` 에 substep #8 추가 (Phase 3-B 실행)

---

## 신규 코드 위치 (제안)

```
3mall-auto-buy/
├── phone_auto/                ← 신규 (Mac 에서 동작)
│   ├── esp32_client.py        # ESP32 REST API 클라이언트 (/click, /tap, /type)
│   ├── camera.py              # OpenCV 웹캠 캡처
│   ├── ocr.py                 # Tesseract / EasyOCR 래퍼
│   ├── lotte_app.py           # lotte 앱 화면 인식 + 액션 시퀀스
│   ├── secure_keypad.py       # 보안 키패드 OCR + 비번 입력
│   ├── coordinates.json       # lotte 앱 화면별 좌표/텍스트 anchor
│   ├── run_lotte_payment.py   # CLI: lotte 7계정 결제 (cart fill 후 호출)
│   └── run_daily.py           # cron 진입점 — rate-check 부터 결제까지 일체
├── esp32_firmware/            # 이미 있음 (펌웨어)
└── card_pins.json             # 신규, gitignored. 7계정 카드 비번 매핑
```

---

## 주의사항

- **카드 비번 보안**: `card_pins.json` 은 반드시 gitignored. 또는 macOS Keychain 활용.
- **lotte 앱 UI 변경**: 좌표 fixed 보다 OCR 기반 anchor (텍스트 위치) 가 robust. UI 업데이트 시 `coordinates.json` 수정.
- **계정 간 간격**: 7계정 sequential 시 lotte 측 비정상 활동 감지 우려 → 계정 간 5초+ 간격 권장.
- **자동 구매 중 웹캠 점유**: FaceTime/Zoom/사진 앱 안 켜기.
- **폰 화면 자동 잠금**: 자동화 중 꺼지면 깨짐 → 설정에서 "잠금 안 함" 또는 "30분+" 권장.
- **lotte.py 잠재 위험** (Phase 3-B 와 별개, 정리 권장): `rate-check/lotte.py:32, 162` 에 하드코드 절대경로 `/Users/jasonkim/...` 있음. 폴더 이동 시 깨짐. 다음 정리 시 `Path(__file__).resolve().parent` 기반으로 변경.

---

## 데이터 흐름 (전체 일일 운영)

```
03:50: Mac wake (pmset)
04:00: cron → run_daily.py 시작
04:00-04:15: rate-check (galleria, hmall, lotte)
04:15-04:20: cart_plan 결과 → 시트 5/17 탭 O~U
04:20-04:35: buy/sulwhasoo.py lotte cart fill (7계정)
04:35-05:00: phone_auto/run_lotte_payment.py (7계정 폰 결제)
              ESP32 + 웹캠 + OCR 으로 자동 결제
05:00: 완료 → 시트에 결제 status 갱신, 사용자 깨면 확인
```

---

## 다음 세션 첫 명령

```
"PHASE_3B_SETUP.md 읽고 Phase α 부터 시작해줘. ESP32 IP 는 <IP>"
```

→ Claude Code 가 자동으로 α 인프라 검증부터 단계별 진행.

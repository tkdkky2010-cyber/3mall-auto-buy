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
| **화면 동기화** | **hybrid — 농협 카드 = 웹캠 OCR (USB 디버그 OFF), 나머지 6 카드사 = scrcpy 미러 OCR (USB 디버그 ON)** |
| 보안 키패드 비번 | OCR 으로 자릿수 위치 추출 → ESP32 클릭 |
| 7계정 분배 | 7계정 × 1조합 각각 (적립 7회) |
| 계정-조합 매핑 | cart_plan 추천 순서 (계정 1=고정조합, 2~7=cart_plan 추천 순) |
| 카드사별 분기 | lotte.py 의 청구할인 카드 자동 감지 결과로 매일 결정. 농협이면 USB 디버그 OFF 필요 (사용자 1초 토글) |

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
- [x] **로지텍 웹캠 OpenCV 인식** (2026-05-17 OK, cam index=1, 1920x1080)
- [x] **opencv-python + easyocr 설치** (Tesseract 5.5.2 기존)
- [x] **scrcpy 4.0 + adb 1.0.41 설치** (`brew install scrcpy android-platform-tools`)
- [ ] **무선 ADB 페어링** (다음 세션): 폰 설정 → 개발자 옵션 → 무선 디버깅 → "페어링 코드로 기기 페어링" → IP:포트 + 6자리 코드 → `adb pair` + `adb connect`
- [ ] **scrcpy 미러링 확인** (`scrcpy --max-size 800`)
- [ ] **Mac 스크린샷 + EasyOCR — 키패드 정확도 검증** (scrcpy 창 캡처 → 숫자 위치 99% 추출 목표)
- [ ] **ESP32 wifi 연결 + IP 확인** (`curl http://<ESP32_IP>/status`)
- [ ] **폰에 ESP32 USB OTG 연결 → `/click` API → 폰 화면 임의 좌표 클릭 동작 확인**

### α 곁가지 — 농협 카드 대비 (USB 디버그 OFF + 웹캠 OCR)
- [ ] 폰 키패드 화면 (USB 디버그 OFF 상태) 웹캠 캡처
- [ ] EasyOCR 으로 키패드 숫자 위치 추출 정확도 검증
- [ ] ⚠️ 검증 시 폰 정 방향 + 키패드 영역 카메라 시야 중앙 + 충분한 조명

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
"PHASE_3B_SETUP.md 읽고 어제 진행 상황 이어서 — 무선 ADB 페어링부터 시작해줘"
```

폰 USB 디버그 켜져있는 상태에서:
1. 폰 설정 → 개발자 옵션 → 무선 디버깅 → "페어링 코드로 기기 페어링" 탭
2. 화면의 IP:포트 + 6자리 페어링 코드 알려주기
3. Mac 에서 `adb pair <ip>:<port>` + 코드 입력 → `adb connect`
4. `scrcpy --max-size 800` 으로 미러링 확인
5. lotte 앱 진입 → 결제 단계 → 키패드 화면 캡처 → EasyOCR 검증

---

## 2026-05-17 진행 메모

**완료**:
- 환경 셋업: opencv-python, easyocr, scrcpy 4.0, adb 1.0.41
- 로지텍/NV76 웹캠 인식 검증
- 카메라 위치 미러 효과는 라이브 미리보기만 — raw OpenCV 캡처는 글자 정상 방향

**결정 사항 (가설)**:
- 카드사별 hybrid (농협 = 웹캠, 나머지 6 = scrcpy)

---

## 2026-05-18 진행 메모 (오늘)

**검증 결과 → 가설 폐기**:
- 무선 ADB 페어링 성공 (Galaxy SM-G9960 Android 15)
- scrcpy 미러링 성공
- **lotte 앱 결제 단계에서 ADB 감지 → 결제 자체 차단** (농협뿐 아니라 lotte 도 차단)
- **scrcpy 길 폐기. 7 카드사 전부 웹캠 OCR 단일 길**

**카메라 셋업 진행 중**:
- macOS 카메라 인덱스 비결정적 (Continuity Camera 끼어들면 충돌)
- iPhone 14 Pro Max Continuity Camera 셋업 시도 — Photo Booth 에 안 뜸
- wifi 동일 (KT_GiGA_8650), iCloud 동일, 연속성 카메라 토글 ON 확인
- 미해결: iPhone 거치 + 충전 + 저전력 모드 OFF 후 재시도 필요

**결정 보류**:
- iPhone Continuity Camera 사용 vs NV76-CM400A 외부 웹캠
- iPhone 은 화질 좋지만 배터리/거리/자동잠금 등 변수 많음
- NV76 은 USB 전원 + 케이블 고정으로 매일 운영 안정성 ↑ — **본 운영에는 NV76 권장**

**오늘 commit**:
- phone_auto/camera_probe.py — 매 인덱스 밝기 측정 + 살아있는 카메라 자동 판별
- phone_auto/keypad_ocr_test.py — cam0 기본 + 자동초점 3초 wait

---

## 2026-05-18 오후 진행 메모

**Continuity Camera 길 폐기 결정**:
- Python AVFoundation (Cursor parent) 에서 iPhone Continuity 안 잡힘
- 원인: Python.app Info.plist `NSCameraUseContinuityCameraDeviceType` 누락 + Cursor 책임 프로세스 attribution
- 해결 시도: Info.plist 키 추가 (Resources/Python.app/Contents/Info.plist) → 효과 없음 (TCC 책임 프로세스 문제)
- Swift CLI `phone_auto/continuity_probe.swift` 빌드 시도 (future reference 용 keep, 사용 X)
- 본질적 문제: Continuity 는 iPhone 모션/BLE 의존 → 매일 4시 무인 자동화에 부적합

**채택: Camo Studio (Reincubate)**:
- iPhone Camo iOS 앱 + Mac Camo Studio
- USB 케이블 연결 → macOS 가 UVC 가상 카메라로 인식 ("Camo Camera", 720×1280)
- OpenCV `cv2.VideoCapture(0)` 으로 즉시 캡처 OK
- Continuity 의 idle/모션 문제 없음

**OCR 검증 결과** (Camo 캡처 기반):
- EasyOCR 단독: 8/10 (1, 9 누락)
- Tesseract 단독: 8/10 (1, 2 누락)
- macOS Vision 단독: 7/10 (셔플마다 다름)
- **3엔진 union: 10/10 ✓**

**다음 단계 (대기 중)**:
- GCP Vision API 셋업 (사용자) → 4엔진 voting + 0~9 distinct 검증
- 셀 기반 ROI 분할 (파란 키패드 검출 → 3×4 grid → 셀별 OCR)
- 다중 프레임 일관성 + PIN dot 검수 함수
- `phone_auto/ocr_keypad.py` 구현 + 5회 셔플 캡처 검증

---

## ★ 재시작 후 진행 명령 (다음 세션 첫 입력)

```
"PHASE_3B_SETUP.md 의 '재시작 후 진행' 섹션 따라 진행. iPhone Continuity Camera
한 번 더 시도해보고 안 되면 NV76-CM400A 웹캠 모드로 즉시 전환."
```

### 재시작 후 절차

**① 환경 확인** (Claude Code 가 자동):
```bash
cd "/Users/jasonkim/Desktop/Vibe Coding/3mall auto buy"
git log --oneline -3
system_profiler SPCameraDataType
```

**② iPhone Continuity 시도** (사용자):
1. iPhone 충전 케이블 연결 (배터리 50%+ 확보)
2. iPhone 설정 → 배터리 → 저전력 모드 OFF 확인
3. iPhone 거치대 / 책상 평평하게 가만히 (가로 권장, 후면 카메라가 Galaxy 향함)
4. iPhone 잠금 해제 + 화면 켠 상태 (자동 잠금 시간 5분+)
5. Mac 의 Photo Booth 열기 → 카메라 메뉴 → "Jason's iPhone" 보이는지 확인

**③ Photo Booth 결과 분기**:
- ✅ iPhone 보임 → `python3 phone_auto/camera_probe.py` → iPhone 인덱스 확인 → keypad_ocr_test.py
- ❌ 안 보임 → iPhone Continuity OFF + NV76 웹캠 USB 연결 → `python3 phone_auto/camera_probe.py`

**④ EasyOCR 키패드 검증**:
- Galaxy 의 lotte 앱 결제 키패드 화면 (또는 임의 보안 키패드)
- `python3 phone_auto/keypad_ocr_test.py`
- 결과 stdout 의 EasyOCR 인식 개수 확인 — 10개 (0~9) 다 잡혀야 OK

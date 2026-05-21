# 2026-05-21 내일 작업 — 폰 자동화 워크플로 확정 + 카드사별 결제 시퀀스

## 어제까지 완성된 것 (2026-05-20)

### 인프라 ✓
- **ESP32-S3 절대좌표 USB HID Touchscreen** ([esp32_firmware/main/main.ino](esp32_firmware/main/main.ino))
  - Digitizer descriptor (Usage Page 0x0D) — 폰이 "Espressif Systems ESP32S3_DEV Touchscreen" 으로 인식
  - 16-bit X/Y 절대좌표 (0~32767 → PHONE_W/H 매핑)
  - `/click {x,y}`, `/tap {x,y,duration_ms}`, `/move {x,y}`, `/type {text}` HTTP endpoint
  - `/swipe {x1,y1,x2,y2,duration_ms}` 코드 추가됨 — **내일 flash 필요** (UART 빠진 상태에서 미업로드)
  - 빌드 옵션: `USBMode=default,CDCOnBoot=default,UploadMode=default` (Serial → UART pins)
  - 업로드: `cd esp32_firmware && PATH="/opt/homebrew/bin:$PATH" arduino-cli upload -b "esp32:esp32:esp32s3:USBMode=default,CDCOnBoot=default,UploadMode=default" -p /dev/cu.usbserial-0001 main/`
- **OCR + camera→phone 좌표 변환** ([phone_auto/screen_ocr.py](phone_auto/screen_ocr.py)) — 신규 모듈
  - macOS Vision Framework (한+영), 모든 텍스트 (x, y) 추출
  - 캘리브레이션: 카메라 frame 안 폰 화면 4 corner → perspective transform
  - `tap_text(esp, "현대카드")` 한 줄로 텍스트 찾고 클릭
- **검증 완료**: "잃어버린 기기 찾기" OCR 검색 → 좌표 변환 → ESP32 탭 → 폰이 실제 페이지 이동 확인 ✓

### 하드웨어 셋업
```
Mac mini USB-C #1 ──→ ESP32 UART 포트 (왼쪽, 전원 + 시리얼)
폰 USB-C ──→ ESP32 Native USB (오른쪽, HID device)
카메라 (Continuity Camera 0번) ──→ Mac mini, 폰 화면 캡쳐
WiFi (KT_GiGA_8650) ──→ Mac + ESP32 + 폰 (HTTP /click)
```
ESP32 WiFi IP: **172.30.1.96** (변할 수 있음, 시작 시 `curl http://172.30.1.96/status` 확인)

### 핵심 발견 (재발 방지)
1. **폰 ↔ ESP32 OTG**: ESP32 가 외부 전원 있으면 폰이 "충전기" 로 인식 → host 모드 거부. 폰 단독 power 일 때만 host 진입.
2. **USB-OTG 모드**: 빌드 옵션 `USBMode=default` 가 USB-OTG (TinyUSB). HID 동작 필수.
3. **CDC On Boot Disabled** 로 빌드해야 Serial 이 UART 핀으로 나옴. Enabled 면 native USB CDC 로 가서 Mac 에서 볼 수 없음.
4. **Mouse 절대좌표는 macOS 에서 무시됨** → Digitizer (touchscreen) 로 변경 (현재). Android 정상 인식.
5. **lotte_login (sulwhasoo.py)** id/pw fill 사이 wait 추가 — 안 그러면 50% 첫시도 실패. [feedback_lotte_login_pacing.md](.claude/projects/.../memory/feedback_lotte_login_pacing.md)

---

## 내일 (5/21) 작업

### 0. 시작 전 셋업 (5분)
```bash
# 1. UART 케이블 다시 Mac 에 꽂기
# 2. /dev/cu.usbserial-0001 보이면 OK

# 3. /swipe 추가된 firmware flash (수정된 main.ino 반영)
cd "/Users/jasonkim/Desktop/Vibe Coding/3mall auto buy/esp32_firmware"
PATH="/opt/homebrew/bin:$PATH" arduino-cli compile -b "esp32:esp32:esp32s3:USBMode=default,CDCOnBoot=default,UploadMode=default" --jobs 1 main/
PATH="/opt/homebrew/bin:$PATH" arduino-cli upload -b "esp32:esp32:esp32s3:USBMode=default,CDCOnBoot=default,UploadMode=default" -p /dev/cu.usbserial-0001 main/

# 4. 폰 ↔ ESP32 native USB 연결, 폰이 ESP32S3_DEV Touchscreen 인식 확인
#    (설정 → 일반 → 하드웨어 키보드 에서 확인 가능)

# 5. ESP32 WiFi IP 확인
curl -s http://172.30.1.96/status   # ← 안 되면 시리얼 모니터로 새 IP 확인
```

### 1. 캘리브레이션 정밀화 (10분)
- 현재 manual calib: `tl=(480,25), tr=(945,25), bl=(480,1050), br=(945,1050)`
- 폰 위치/카메라 위치 그대로면 그대로 사용 가능
- 위치 변하면: `phone_auto/_tmp/calibration.json` 갱신
- 자동 검출 시도: 폰 화면 흰색 배경 띄우고 (예: 설정) `python3 -m phone_auto.screen_ocr calibrate-auto`
- 또는 manual: `python3 -m phone_auto.screen_ocr calibrate-manual --tl X,Y --tr X,Y --bl X,Y --br X,Y`
- **검증**: 화면 OCR → tap 1개 → 캡쳐 후 비교
  ```bash
  ESP32_IP=172.30.1.96 python3 -m phone_auto.screen_ocr tap --query "<텍스트>"
  ```

### 2. 카드사별 결제 워크플로 정의 ⭐ 핵심
**사용자가 직접 작성/구술 — 각 카드사 결제 단계 명세**

각 카드사마다 다음 흐름:
```
[STEP 1] 바탕화면에서 OO카드 앱 아이콘 누름
[STEP 2] 앱 열림 대기 → "결제" 메뉴 누름 (또는 메인 화면 결제 버튼)
[STEP 3] 결제 정보 입력 화면 (가맹점 / 금액 등)
[STEP 4] "결제하기" 또는 "다음" 버튼 누름
[STEP 5] 결제 코드 7자리 입력 (OCR 키패드, 기존 pin_entry 재사용)
[STEP 6] 결제 비밀번호 6자리 입력
[STEP 7] 완료 화면 확인
```

각 STEP 의 예상 화면 텍스트 (OCR 라벨) + 탭 라벨 미리 정리. 예:
```python
HYUNDAI_WORKFLOW = [
    ("tap_text", {"query": "현대카드"}),       # 앱 아이콘
    ("wait_for_text", {"query": "결제", "timeout": 10}),
    ("tap_text", {"query": "결제"}),
    ("wait_for_text", {"query": "결제하기"}),
    ("tap_text", {"query": "결제하기"}),
    ("enter_pin", {"card_key": "hyundai_code7", "pin": "1234567"}),
    ("wait_for_text", {"query": "결제 완료", "timeout": 30}),
]
```

대상 카드사 (활성):
- 현대카드, 하나카드, KB국민카드, NH농협카드, 롯데카드, 삼성카드, BC카드, 신한카드, 페이북

### 3. 워크플로 runner 구현 (30분 ~ 1시간)
- `phone_auto/workflow.py` 신규 — Step 시퀀스 실행 + 각 단계 retry/verify
- Primitives:
  - `tap_text(query)` — 이미 구현됨
  - `wait_for_text(query, timeout=10)` — OCR 루프, 텍스트 나타날 때까지 대기
  - `swipe(x1,y1,x2,y2)` — firmware 새 endpoint, 홈으로 가는 swipe up 등
  - `enter_pin(card_key, pin)` — 기존 `pin_entry.enter_pin` 활용
  - `sleep(sec)`, `verify_text(query)` (단순 OCR 검증)
- CLI: `python3 -m phone_auto.workflow run --card hyundai`

### 4. E2E 테스트 (30분)
- 한 카드사 (예: 현대카드) full flow 검증 — DRY_PAYMENT=true 로 결제 직전까지만 (실제 청구 X)
- 실패 지점 디버그 (OCR 인식 안 됨, 좌표 오프 등)
- 워크플로 정확도 → 다른 카드사 적용

### 5. (선택) 자율 탐색 모드 — "쉬는 동안 폰이 알아서 카드사 앱 시리즈 처리"
- ESP32_IP, 폰 캘리브레이션, 카드사 워크플로 셋만 정의되면 PC 가 자동 실행
- 사용자는 카드 비밀번호 / OTP 만 외부 환경에서 입력 (또는 미리 hsmaster 에 저장)

---

## 알려진 이슈 / TODO

1. **뒤로가기 (Back)**: ESC 키 HID 보내봤지만 Galaxy S21+ 가 안 받음 (`Keyboard.write(0x1b)`). 다른 방법 시도:
   - HID Keyboard `press(KEY_ESC)` (write 대신 press/release)
   - 또는 swipe right (왼쪽 가장자리에서) 으로 gesture back
   - 또는 OCR 로 "<" 버튼 위치 정확히 잡아 탭 (현재 좌표 off)
2. **Home 으로 가기**: gesture nav 라면 swipe up (firmware 의 새 /swipe endpoint). 또는 nav bar 가운데 버튼 tap.
3. **Calibration 의존성**: 폰/카메라 위치 살짝 변하면 좌표 어긋남. 자동 검출 강화 (현재 흰배경 가정만 작동) — ORB keypoint align 으로 reference frame 대비 transform 정밀화.
4. **OCR 정확도**: 한글 키패드 외 일반 텍스트는 conf 0.5 인 경우 많음. 멀티 프레임 평균 또는 EasyOCR 보조 활용 검토.
5. **Card 비밀번호 어떻게 입력**: 사용자가 매번 수동 입력? 또는 hsmaster 에 카드별 PIN 저장하고 자동? 보안 정책 확인.

---

## 파일 위치

- `esp32_firmware/main/main.ino` — ESP32 펌웨어
- `phone_auto/screen_ocr.py` — 일반 OCR + 좌표 변환 + tap (신규)
- `phone_auto/ocr_keypad.py` — 키패드 전용 OCR (기존)
- `phone_auto/pin_entry.py` — PIN 입력 (기존)
- `phone_auto/esp32_client.py` — ESP32 HTTP wrapper (기존)
- `phone_auto/_tmp/calibration.json` — 캘리브레이션 저장 (사용 중)

## 카메라 / 폰 셋업 메모
- 폰 거치: 한 번 더 위치 변경 시 캘리브레이션 다시
- Camo 또는 Continuity Camera (cam idx 0, 1920x1080)
- 폰 글자 크기: 키운 상태 / 명확한 글씨체로 변경됨 (OCR 정확도 위해 유지)

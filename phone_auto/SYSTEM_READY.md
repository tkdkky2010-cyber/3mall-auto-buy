# 폰 자율 조작 시스템 — 완성된 인프라

## ⛔ 절대 원칙 — 좌표 추정 금지 (2026-05-22)

**폰 조작 시 phone 좌표를 추정해서 click 보내지 말 것.**

- ❌ 금지: `wf.tap_xy(113, 648)` 처럼 머릿속/계산식으로 얻은 좌표 사용
- ❌ 금지: 다른 anchor 로부터 비례식·perspective 추정한 좌표 사용
- ❌ 금지: "한 행 위" "column 0 중심" 같은 grid 추정으로 좌표 결정
- ✅ 강제: **매 탭 직전 카메라 캡쳐 + OCR + 캘리브 변환** 으로 정확 좌표 획득
  → `wf.tap_text("현대카드")` 또는 동등한 OCR-based 호출만 사용
- ✅ 강제: OCR 으로 잡히지 않는 아이콘(X 버튼·뒤로가기 등)은 캡쳐 → 사용자 확인 → manual calibrate 후 OCR-가능 라벨 인접점으로 도달

**왜:** 추정 좌표는 한 행/한 열 어긋나서 다른 카드/앱이 눌리거나 시스템 동작이 일어남. 실패의 90% 가 캘리브 부정확 → 추정 보정 → 또 어긋남 → 시간 낭비. OCR 텍스트 위치는 항상 픽셀 단위 정확하므로 캘리브만 한 번 정확하면 모든 탭 정확.

**한 번만 어긋나도 카드 잘못 결제됨. 추정 금지.**



## 검증된 동작 (2026-05-20)

| 동작 | 검증 |
|---|---|
| ESP32 USB Digitizer HID 폰 인식 | ✓ "Espressif Systems ESP32S3_DEV Touchscreen" |
| 절대좌표 탭 (1080×2400 → HID 32767) | ✓ 5개 위치 모두 작동 |
| OCR 한+영 텍스트 추출 (macOS Vision) | ✓ 28개 텍스트 검출 (홈화면), 17개 (설정) |
| 카메라 → 폰 좌표 변환 (perspective transform) | ✓ tl=(480,25), tr=(945,25), bl=(480,1050), br=(945,1050) |
| OCR 라벨 → 탭 | ✓ "잃어버린 기기 찾기" → 페이지 이동 |
| 메뉴 진입 | ✓ "연결" → Wi-Fi/Bluetooth 페이지 |
| 추가 메뉴 진입 | ✓ "삼성 계정" → samsung Account 페이지 |
| Back 탭 (phone 60, 230) | ✓ 연결 페이지 → settings 메인 |

## 시스템 구성

```
[Mac mini]
  ├─ HTTP 클라이언트 (curl, phone_auto.esp32_client)
  ├─ 카메라 (cam 0 Continuity, 1920×1080)
  ├─ phone_auto/screen_ocr.py  ─── Vision OCR + 좌표 변환
  ├─ phone_auto/workflow.py    ─── Step 시퀀스 runner
  └─ phone_auto/pin_entry.py   ─── 숫자 키패드 자동 입력

       │ WiFi (KT_GiGA_8650)
       ▼
[ESP32-S3 DevKitC-1] @ 172.30.1.96
  ├─ /click /tap /move /swipe /type 엔드포인트
  ├─ Custom HID Digitizer descriptor (절대좌표)
  └─ USB OTG device 모드
       │
       │ USB-C OTG
       ▼
[Galaxy S21+ 1080×2400] — Touchscreen HID host
```

## 사용 가능한 명령 (오늘 검증된 것)

### 1. ESP32 직접 명령 (HTTP)
```bash
# 상태
curl http://172.30.1.96/status
# 탭
curl -X POST http://172.30.1.96/click -H "Content-Type: application/json" -d '{"x":540,"y":1200}'
# 긴 탭 (long press)
curl -X POST http://172.30.1.96/tap -d '{"x":540,"y":1200,"duration_ms":500}' -H "Content-Type: application/json"
# 키보드 (영문/숫자)
curl -X POST http://172.30.1.96/type -d '{"text":"1234"}' -H "Content-Type: application/json"
# 스와이프 — firmware /swipe endpoint 추가 코드 작성됨. 내일 flash 후 사용 가능.
curl -X POST http://172.30.1.96/swipe -d '{"x1":540,"y1":2200,"x2":540,"y2":1200,"duration_ms":300}' -H "Content-Type: application/json"
```

### 2. screen_ocr.py — OCR 기반 조작
```bash
# 현재 화면 OCR (텍스트 + 카메라 좌표)
python3 -m phone_auto.screen_ocr ocr

# 캘리브레이션 (자동 / 수동)
python3 -m phone_auto.screen_ocr calibrate-auto
python3 -m phone_auto.screen_ocr calibrate-manual --tl 480,25 --tr 945,25 --bl 480,1050 --br 945,1050

# 텍스트 탭 (dry-run / 실제)
python3 -m phone_auto.screen_ocr dry-tap --query "현대카드"
ESP32_IP=172.30.1.96 python3 -m phone_auto.screen_ocr tap --query "현대카드"
```

### 3. workflow.py — 시퀀스 자동화
```bash
# 화면 캡쳐 + OCR
python3 -m phone_auto.workflow capture

# 텍스트 탭
python3 -m phone_auto.workflow tap --query "현대카드"

# 좌표 직접 탭
python3 -m phone_auto.workflow tap --x 540 --y 1200

# Back / Home (swipe 사용 — firmware swipe flash 후)
python3 -m phone_auto.workflow back
python3 -m phone_auto.workflow home

# PIN 입력 (셔플 키패드 OCR)
python3 -m phone_auto.workflow enter-pin --card hyundai_code7 --pin 1234567

# 자율 탐색 (카드앱 찾고 탭 시도)
python3 -m phone_auto.workflow explore
```

### 4. Python API
```python
from phone_auto.workflow import Workflow
from phone_auto.esp32_client import ESP32Client

esp = ESP32Client('172.30.1.96')
wf = Workflow(esp, cam_idx=0)

wf.capture("시작")
wf.tap_text("현대카드")
wf.wait_for_text("결제", timeout=10)
wf.tap_text("결제")
wf.enter_pin("hyundai_code7", "1234567")
wf.verify_text("결제 완료")
```

## 사용자가 내일 채울 부분 — 카드사별 결제 시퀀스

워크플로 인프라는 다 만들어져 있음. 각 카드사 앱의 실제 화면 흐름만 사용자가 정의하면 됨:

```python
# phone_auto/workflows/hyundai.py (예시 - 사용자가 카드 앱 열어보고 작성)
def hyundai_payment(wf, code7, pin6):
    wf.tap_text("현대카드")              # 바탕화면 아이콘
    wf.wait_for_text("결제", timeout=15)
    wf.tap_text("결제")
    # ↓↓↓ 사용자 확인 필요 ↓↓↓
    # 결제 화면에 어떤 메뉴가 보임? "QR결제" / "간편결제" / "송금" ?
    # 결제 금액 어떻게 입력? 자동? 매장 QR?
    # 7자리 코드 입력 직전 어떤 텍스트?
    wf.wait_for_text("결제 코드", timeout=10)
    wf.enter_pin("hyundai_code7", code7)
    wf.wait_for_text("비밀번호", timeout=10)
    wf.enter_pin("hyundai_pin6", pin6)
    return wf.verify_text("결제 완료")
```

다른 카드사: 하나, KB Pay, NH pay, 롯데, 삼성, BC카드 — 각 앱 한 번씩 들어가서 다음 정보 수집:

1. **앱 아이콘 라벨** (바탕화면에서) — 보통 카드사 이름 그대로 OK
2. **메인 화면 결제 진입 버튼** — "결제", "QR결제", "Pay" 등
3. **결제 코드 입력 화면 라벨** — wait_for_text 용
4. **결제 코드 7자리 / 비밀번호 6자리 키패드 종류** — preset key (`hyundai_code7`, `hana_pin6` 등) 이미 정의됨
5. **결제 완료 확인 텍스트** — verify_text 용

## 알려진 한계 + 개선 예정

1. **Back/Home 키 (해결됨)** — 3-key nav bar 사용. ADB `KEYCODE_BACK` / `KEYCODE_HOME` 호출 (workflow.back() / workflow.home()). swipe gesture 안 씀.
2. **OCR conf 0.5 항목 다수** — 정확도 위해 동일 라벨이 정확히 한 번만 등장한다 가정. 중복 시 첫 번째 선택. 멀티프레임 voting 추가 가능.
3. **캘리브레이션 의존성** — 폰/카메라 위치 변하면 재측정 필요. ORB keypoint align 추가 시 robust해짐.
4. **결제 보안번호 / 카드 비밀번호 어떻게 저장?** — 사용자 정책 결정 필요. 현재는 CLI argv 로 매번 입력.
5. **/swipe 엔드포인트 미배포** — main.ino 에 코드는 있지만 5/20 종료 시점 UART 빠진 상태라 flash 못 함. 내일 첫 작업으로.

## 결제 워크플로 전체 그림 (사용자 정의 대기)

```
[1] PC: workflow runner 시작 (예: hyundai_payment("0123456", "789012"))
[2] PC: 캘리브레이션 로드, 카메라 캡쳐
[3] OCR: "현대카드" 라벨 찾기 → 좌표 (px, py)
[4] ESP32: 폰 (px, py) 탭 → USB HID Digitizer 절대 탭 이벤트
[5] 폰: 현대카드 앱 실행 (1-3초)
[6] PC: 재캡쳐, "결제" 텍스트 wait_for
[7] OCR: "결제" 찾기 → 탭
[8] (반복) 각 화면 OCR + 탭 + verify
[9] 결제 코드 입력 화면 도달 → pin_entry.enter_pin(card_key, "0123456")
   - 셔플 키패드 OCR (이미 작동, 9개 카드사 preset)
   - 각 digit 위치 찾고 7번 탭 + 각 자릿수 후 dot 검증
[10] 결제 비밀번호 입력 → enter_pin(card_key, "789012")
[11] PC: "결제 완료" verify → 성공/실패 보고
```

모든 단계 인프라 ✓. 4 ~ 10 사이 카드사 앱 특화 흐름만 사용자 입력 대기.

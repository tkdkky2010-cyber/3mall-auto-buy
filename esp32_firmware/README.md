# ESP32-S3 펌웨어

ESP32-S3가 USB HID 마우스/키보드로 폰을 자동입력 + WiFi REST API로 PC 명령 수신.

## Arduino IDE 셋업

1. Arduino IDE 2.x 설치
2. File → Preferences → "Additional boards manager URLs":
   `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
3. Tools → Board → Boards Manager → "esp32" by Espressif Systems 설치 (v2.0.5+)
4. 라이브러리 설치 (Library Manager):
   - ArduinoJson (Benoit Blanchon)

## 보드 설정 (중요!)

Tools 메뉴:
- **Board**: ESP32S3 Dev Module
- **USB Mode**: USB-OTG (TinyUSB)
- **USB CDC On Boot**: Enabled
- **USB Firmware MSC On Boot**: Disabled
- **USB DFU On Boot**: Disabled
- **Upload Mode**: UART0 / Hardware CDC
- **Upload Speed**: 921600

## 펌웨어 업로드

1. `main.ino` 열기
2. `WIFI_SSID`, `WIFI_PASS` 위쪽에서 본인 WiFi 정보 입력
3. ESP32-S3을 USB-C로 PC에 연결
4. Tools → Port에서 USB 시리얼 포트 선택
5. Upload (→ 화살표)
6. 업로드 끝나면 Serial Monitor 열어서 WiFi 연결 확인 + IP 주소 메모

## 본 운영

1. PC에서 ESP32 USB 빼기
2. ESP32 USB-C ↔ 폰 USB-C OTG 케이블로 연결
3. 폰이 ESP32에 5V 공급 → ESP32 부팅
4. ESP32가 WiFi 자동 연결
5. PC에서 `curl http://<ESP32_IP>/status` 로 상태 확인

## 트러블슈팅

- **부팅 안 됨**: USB Mode를 "USB-OTG"로 정확히 설정했는지 확인
- **WiFi 연결 실패**: SSID/PW 오타, 또는 5GHz 와이파이만 있으면 안 됨 (ESP32-S3은 2.4GHz만)
- **USB HID 동작 X**: USB CDC On Boot Enabled 확인
- **폰이 ESP32 인식 안 함**: USB-C 케이블 데이터 지원 확인 (충전 전용 X)

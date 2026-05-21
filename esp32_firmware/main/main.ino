/* ESP32-S3 USB HID Absolute Pointer + WiFi REST Server
 *
 * 절대좌표 USB HID (16-bit X/Y, 0~32767) — Android 가 마우스 절대 좌표로 인식.
 * 키보드 = 표준 USBHIDKeyboard. 마우스 = custom USBHIDDevice (raw HID).
 *
 * Endpoints:
 *   GET  /status              -- {"wifi":"ok","ip":"...","ready":true}
 *   POST /click  {"x":N,"y":N}                    -- 절대좌표 클릭
 *   POST /tap    {"x":N,"y":N,"duration_ms":N}    -- 절대좌표 긴 탭
 *   POST /move   {"x":N,"y":N}                    -- 커서 이동만
 *   POST /type   {"text":"..."}                   -- 키보드 입력 (영문/숫자)
 *
 * 빌드 옵션 (필수):
 *   - Board: ESP32S3 Dev Module (FQBN: esp32:esp32:esp32s3)
 *   - USB Mode: USB-OTG (TinyUSB)      [USBMode=default]
 *   - USB CDC On Boot: Enabled         [CDCOnBoot=cdc]
 *   - Upload Mode: UART0 / Hardware CDC [UploadMode=default]
 */

#include <WiFi.h>
#include <WebServer.h>
#include <ArduinoJson.h>
#include <USB.h>
#include <USBHID.h>
#include <USBHIDKeyboard.h>

const char* WIFI_SSID = "KT_GiGA_8650";
const char* WIFI_PASS = "1dbc4ec673";

// 폰 화면 해상도 (절대좌표 → HID 16-bit 매핑 기준)
const int PHONE_W = 1080;
const int PHONE_H = 2400;

// HID Report Descriptor — Digitizer (TouchScreen finger)
// macOS/Android 둘 다 절대좌표 touch event 로 인식.
// Report 형식 (5 bytes after Report ID):
//   byte0: bit0=Tip Switch (눌림), bit1=In Range (커서 위에 있음)
//   byte1-2: X (16-bit, 0~32767)
//   byte3-4: Y (16-bit, 0~32767)
static const uint8_t ABS_MOUSE_REPORT_DESCRIPTOR[] = {
    0x05, 0x0D,        // Usage Page (Digitizer)
    0x09, 0x04,        // Usage (Touch Screen)
    0xA1, 0x01,        // Collection (Application)
    0x85, 0x01,        //   Report ID (1)
    0x09, 0x22,        //   Usage (Finger)
    0xA1, 0x02,        //   Collection (Logical)
    0x09, 0x42,        //     Usage (Tip Switch)
    0x09, 0x32,        //     Usage (In Range)
    0x15, 0x00,        //     Logical Minimum (0)
    0x25, 0x01,        //     Logical Maximum (1)
    0x75, 0x01,        //     Report Size (1)
    0x95, 0x02,        //     Report Count (2)
    0x81, 0x02,        //     Input (Data, Var, Abs)
    0x75, 0x01,        //     Report Size (1)
    0x95, 0x06,        //     Report Count (6) -- padding
    0x81, 0x03,        //     Input (Const)
    0x05, 0x01,        //     Usage Page (Generic Desktop)
    0x09, 0x30,        //     Usage (X)
    0x16, 0x00, 0x00,  //     Logical Minimum (0)
    0x26, 0xFF, 0x7F,  //     Logical Maximum (32767)
    0x75, 0x10,        //     Report Size (16)
    0x95, 0x01,        //     Report Count (1)
    0x81, 0x02,        //     Input (Data, Var, Abs)
    0x09, 0x31,        //     Usage (Y)
    0x16, 0x00, 0x00,  //     Logical Minimum (0)
    0x26, 0xFF, 0x7F,  //     Logical Maximum (32767)
    0x75, 0x10,        //     Report Size (16)
    0x95, 0x01,        //     Report Count (1)
    0x81, 0x02,        //     Input (Data, Var, Abs)
    0xC0,              //   End Collection
    0xC0               // End Collection
};

USBHID HID;  // 전역 HID 인스턴스 (Mouse + Keyboard 공유)

class AbsoluteMouse : public USBHIDDevice {
public:
    AbsoluteMouse() {
        static bool added = false;
        if (!added) {
            added = true;
            HID.addDevice(this, sizeof(ABS_MOUSE_REPORT_DESCRIPTOR));
        }
    }
    void begin() { HID.begin(); }
    uint16_t _onGetDescriptor(uint8_t* buffer) override {
        memcpy(buffer, ABS_MOUSE_REPORT_DESCRIPTOR, sizeof(ABS_MOUSE_REPORT_DESCRIPTOR));
        return sizeof(ABS_MOUSE_REPORT_DESCRIPTOR);
    }

    // 5-byte report: [flags(tip,inrange,pad)][x_lo][x_hi][y_lo][y_hi]
    //   tip = 1 (눌림) / 0 (떼짐)
    //   inrange = 1 (항상 hover 상태)
    void sendReport(uint8_t tip_down, uint16_t x, uint16_t y) {
        uint8_t report[5];
        // bit0=Tip Switch, bit1=In Range
        report[0] = (tip_down ? 0x01 : 0x00) | 0x02;  // 항상 In Range
        report[1] = (uint8_t)(x & 0xFF);
        report[2] = (uint8_t)((x >> 8) & 0xFF);
        report[3] = (uint8_t)(y & 0xFF);
        report[4] = (uint8_t)((y >> 8) & 0xFF);
        HID.SendReport(0x01, report, sizeof(report));  // Report ID = 1
    }

    // 픽셀 (PHONE_W × PHONE_H) → 0~32767 변환
    uint16_t toAbs(int pixel, int range) {
        if (pixel < 0) pixel = 0;
        if (pixel >= range) pixel = range - 1;
        return (uint16_t)(((uint32_t)pixel * 32767) / (uint32_t)(range - 1));
    }

    void moveTo(int x, int y) {
        // hover: tip 안 누름 (in-range only)
        sendReport(0, toAbs(x, PHONE_W), toAbs(y, PHONE_H));
    }
    void click(int x, int y) {
        uint16_t ax = toAbs(x, PHONE_W), ay = toAbs(y, PHONE_H);
        sendReport(0, ax, ay);   // hover at position
        delay(15);
        sendReport(1, ax, ay);   // tip down
        delay(50);
        sendReport(0, ax, ay);   // tip up
    }
    void tap(int x, int y, int duration_ms) {
        uint16_t ax = toAbs(x, PHONE_W), ay = toAbs(y, PHONE_H);
        sendReport(0, ax, ay);
        delay(15);
        sendReport(1, ax, ay);
        delay(duration_ms);
        sendReport(0, ax, ay);
    }
    // (x1,y1) → (x2,y2) 드래그/스와이프. steps 단계로 부드럽게.
    void swipe(int x1, int y1, int x2, int y2, int duration_ms, int steps = 20) {
        uint16_t ax = toAbs(x1, PHONE_W), ay = toAbs(y1, PHONE_H);
        sendReport(0, ax, ay);
        delay(15);
        sendReport(1, ax, ay);  // tip down at start
        delay(20);
        int step_delay = duration_ms / steps;
        if (step_delay < 5) step_delay = 5;
        for (int i = 1; i <= steps; ++i) {
            int x = x1 + ((x2 - x1) * i) / steps;
            int y = y1 + ((y2 - y1) * i) / steps;
            sendReport(1, toAbs(x, PHONE_W), toAbs(y, PHONE_H));
            delay(step_delay);
        }
        sendReport(0, toAbs(x2, PHONE_W), toAbs(y2, PHONE_H));  // tip up at end
    }
};

AbsoluteMouse Mouse;
USBHIDKeyboard Keyboard;
WebServer server(80);

void handleStatus() {
    JsonDocument doc;
    doc["wifi"] = WiFi.status() == WL_CONNECTED ? "ok" : "disconnected";
    doc["ip"] = WiFi.localIP().toString();
    doc["ready"] = true;
    doc["phone_w"] = PHONE_W;
    doc["phone_h"] = PHONE_H;
    doc["hid"] = "absolute_mouse_v1";
    String out;
    serializeJson(doc, out);
    server.send(200, "application/json", out);
}

void handleClick() {
    if (!server.hasArg("plain")) { server.send(400, "text/plain", "no body"); return; }
    JsonDocument doc;
    deserializeJson(doc, server.arg("plain"));
    int x = doc["x"] | -1, y = doc["y"] | -1;
    if (x < 0 || y < 0) { server.send(400, "text/plain", "bad coords"); return; }
    Serial.printf("[click] (%d, %d)\n", x, y);
    Mouse.click(x, y);
    server.send(200, "application/json", "{\"ok\":true}");
}

void handleTap() {
    if (!server.hasArg("plain")) { server.send(400, "text/plain", "no body"); return; }
    JsonDocument doc;
    deserializeJson(doc, server.arg("plain"));
    int x = doc["x"] | -1, y = doc["y"] | -1;
    int dur = doc["duration_ms"] | 100;
    if (x < 0 || y < 0) { server.send(400, "text/plain", "bad coords"); return; }
    Serial.printf("[tap] (%d, %d) %dms\n", x, y, dur);
    Mouse.tap(x, y, dur);
    server.send(200, "application/json", "{\"ok\":true}");
}

void handleMove() {
    if (!server.hasArg("plain")) { server.send(400, "text/plain", "no body"); return; }
    JsonDocument doc;
    deserializeJson(doc, server.arg("plain"));
    int x = doc["x"] | -1, y = doc["y"] | -1;
    if (x < 0 || y < 0) { server.send(400, "text/plain", "bad coords"); return; }
    Serial.printf("[move] (%d, %d)\n", x, y);
    Mouse.moveTo(x, y);
    server.send(200, "application/json", "{\"ok\":true}");
}

void handleSwipe() {
    if (!server.hasArg("plain")) { server.send(400, "text/plain", "no body"); return; }
    JsonDocument doc;
    deserializeJson(doc, server.arg("plain"));
    int x1 = doc["x1"] | -1, y1 = doc["y1"] | -1;
    int x2 = doc["x2"] | -1, y2 = doc["y2"] | -1;
    int dur = doc["duration_ms"] | 300;
    if (x1 < 0 || y1 < 0 || x2 < 0 || y2 < 0) { server.send(400, "text/plain", "bad coords"); return; }
    Serial.printf("[swipe] (%d,%d) → (%d,%d) %dms\n", x1, y1, x2, y2, dur);
    Mouse.swipe(x1, y1, x2, y2, dur);
    server.send(200, "application/json", "{\"ok\":true}");
}

void handleType() {
    if (!server.hasArg("plain")) { server.send(400, "text/plain", "no body"); return; }
    JsonDocument doc;
    deserializeJson(doc, server.arg("plain"));
    const char* text = doc["text"] | "";
    if (!text || !*text) { server.send(400, "text/plain", "no text"); return; }
    Serial.printf("[type] '%s'\n", text);
    for (const char* p = text; *p; ++p) {
        Keyboard.write(*p);
        delay(20);
    }
    server.send(200, "application/json", "{\"ok\":true}");
}

void setup() {
    Serial.begin(115200);
    delay(2000);
    Serial.println("=== boot ===");
    Serial.println("FW: absolute_mouse_v1");

    // HID 초기화 (Mouse + Keyboard 모두 등록 후 USB.begin)
    Mouse.begin();
    Keyboard.begin();
    USB.begin();
    Serial.println("HID init done");

    delay(3000);  // USB enumerate 안정화

    Serial.println("starting WiFi (non-blocking)");
    Serial.flush();

    WiFi.persistent(false);
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    Serial.println("WiFi.begin() returned, continuing setup");

    server.on("/status", HTTP_GET, handleStatus);
    server.on("/click", HTTP_POST, handleClick);
    server.on("/tap", HTTP_POST, handleTap);
    server.on("/move", HTTP_POST, handleMove);
    server.on("/swipe", HTTP_POST, handleSwipe);
    server.on("/type", HTTP_POST, handleType);
    server.begin();
    Serial.println("HTTP server on :80");
}

void loop() {
    server.handleClient();
    static unsigned long last = 0;
    static unsigned long lastReconnect = 0;
    if (millis() - last > 5000) {
        last = millis();
        Serial.printf("[status] WiFi=%s IP=%s\n",
            WiFi.status() == WL_CONNECTED ? "OK" : "no",
            WiFi.localIP().toString().c_str());
    }
    if (WiFi.status() != WL_CONNECTED && millis() - lastReconnect > 15000) {
        lastReconnect = millis();
        Serial.println("[reconnect] WiFi.begin() retry");
        WiFi.disconnect();
        WiFi.begin(WIFI_SSID, WIFI_PASS);
    }
}

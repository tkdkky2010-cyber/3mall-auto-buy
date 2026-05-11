/* ESP32-S3 USB HID Mouse + WiFi REST Server
 *
 * PC에서 HTTP 명령 받아서 USB HID로 폰에 클릭/이동/타이핑 전달.
 *
 * Endpoints:
 *   GET  /status              -- {"wifi": "ok", "usb": "ok", "ip": "..."}
 *   POST /click  {"x":N,"y":N}        -- 절대 좌표 클릭
 *   POST /tap    {"x":N,"y":N,"duration_ms":N} -- 짧은 탭
 *   POST /move   {"x":N,"y":N}        -- 커서 이동만
 *   POST /type   {"text":"1234567"}  -- 키보드 입력 (USB HID Keyboard)
 *
 * 좌표 시스템:
 *   - USB HID 절대 좌표 모드: 0~32767 (16비트 signed)
 *   - 폰이 자기 화면 해상도에 맞춰 매핑함
 *   - 우리는 폰 픽셀 좌표를 0~32767로 변환해서 전송
 *
 * 빌드 환경 (Arduino IDE):
 *   - 보드: ESP32S3 Dev Module
 *   - USB Mode: USB-OTG (TinyUSB)
 *   - USB CDC On Boot: Enabled
 *   - 라이브러리: 기본 USBHIDMouse, USBHIDKeyboard 포함 (Arduino ESP32 v2.0.5+)
 */

#include <WiFi.h>
#include <WebServer.h>
#include <ArduinoJson.h>
#include <USB.h>
#include <USBHIDMouse.h>
#include <USBHIDKeyboard.h>

// ───── WiFi 설정 (이 부분만 수정) ─────
const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASS = "YOUR_WIFI_PASS";

// 폰 화면 해상도 — 캘리브레이션 시 측정 (Galaxy S22 = 1080x2400)
const int PHONE_W = 1080;
const int PHONE_H = 2400;

USBHIDMouse Mouse;
USBHIDKeyboard Keyboard;
WebServer server(80);

// 폰 픽셀 좌표 → HID 절대 좌표 (0~32767) 변환
int16_t toAbs(int pixel, int range) {
  return (int16_t)((long)pixel * 32767 / range);
}

void mouseMoveAbs(int x, int y) {
  // ESP32 USBHIDMouse는 상대 좌표만 지원하는 경우가 많음
  // 절대 좌표 → 큰 단위 이동으로 시뮬레이션 (한계: 정확도)
  // 또는 TinyUSB 직접 사용해서 absolute mode HID descriptor 사용
  // 여기는 simple version: relative move + reset 패턴

  // 화면 좌상단으로 reset (큰 negative move)
  Mouse.move(-32767, -32767);
  delay(20);
  Mouse.move(-32767, -32767);
  delay(20);
  // 목표 위치로 이동
  Mouse.move(toAbs(x, PHONE_W), toAbs(y, PHONE_H));
  delay(20);
}

void handleStatus() {
  JsonDocument doc;
  doc["wifi"] = WiFi.status() == WL_CONNECTED ? "ok" : "disconnected";
  doc["ip"] = WiFi.localIP().toString();
  doc["usb"] = "ready";  // TODO: 실제 USB host 연결 확인
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
  mouseMoveAbs(x, y);
  delay(30);
  Mouse.click(MOUSE_LEFT);
  server.send(200, "application/json", "{\"ok\":true}");
}

void handleTap() {
  if (!server.hasArg("plain")) { server.send(400, "text/plain", "no body"); return; }
  JsonDocument doc;
  deserializeJson(doc, server.arg("plain"));
  int x = doc["x"] | -1, y = doc["y"] | -1;
  int dur = doc["duration_ms"] | 50;
  if (x < 0 || y < 0) { server.send(400, "text/plain", "bad coords"); return; }
  mouseMoveAbs(x, y);
  delay(30);
  Mouse.press(MOUSE_LEFT);
  delay(dur);
  Mouse.release(MOUSE_LEFT);
  server.send(200, "application/json", "{\"ok\":true}");
}

void handleMove() {
  if (!server.hasArg("plain")) { server.send(400, "text/plain", "no body"); return; }
  JsonDocument doc;
  deserializeJson(doc, server.arg("plain"));
  int x = doc["x"] | -1, y = doc["y"] | -1;
  if (x < 0 || y < 0) { server.send(400, "text/plain", "bad coords"); return; }
  mouseMoveAbs(x, y);
  server.send(200, "application/json", "{\"ok\":true}");
}

void handleType() {
  if (!server.hasArg("plain")) { server.send(400, "text/plain", "no body"); return; }
  JsonDocument doc;
  deserializeJson(doc, server.arg("plain"));
  const char* text = doc["text"] | "";
  if (!text || !*text) { server.send(400, "text/plain", "no text"); return; }
  for (const char* p = text; *p; ++p) {
    Keyboard.write(*p);
    delay(20);
  }
  server.send(200, "application/json", "{\"ok\":true}");
}

void setup() {
  Serial.begin(115200);
  delay(500);

  // USB HID 초기화 (마우스 + 키보드)
  USB.begin();
  Mouse.begin();
  Keyboard.begin();

  // WiFi 연결
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("WiFi connecting...");
  int retries = 0;
  while (WiFi.status() != WL_CONNECTED && retries < 60) {
    delay(500);
    Serial.print(".");
    retries++;
  }
  Serial.println();
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi 연결 실패");
    return;
  }
  Serial.print("WiFi OK. IP: ");
  Serial.println(WiFi.localIP());

  // HTTP routes
  server.on("/status", HTTP_GET, handleStatus);
  server.on("/click", HTTP_POST, handleClick);
  server.on("/tap", HTTP_POST, handleTap);
  server.on("/move", HTTP_POST, handleMove);
  server.on("/type", HTTP_POST, handleType);
  server.begin();
  Serial.println("HTTP server started on port 80");
}

void loop() {
  server.handleClient();
}

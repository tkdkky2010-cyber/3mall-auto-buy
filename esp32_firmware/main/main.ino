/* ESP32-S3 USB HID Mouse + WiFi REST Server
 *
 * PC에서 HTTP 명령 받아서 USB HID로 폰에 클릭/이동/타이핑 전달.
 *
 * Endpoints:
 *   GET  /status              -- {"wifi":"ok","ip":"..."}
 *   POST /click  {"x":N,"y":N}                  -- 좌표 클릭
 *   POST /tap    {"x":N,"y":N,"duration_ms":N}  -- 짧은 탭
 *   POST /move   {"x":N,"y":N}                  -- 커서 이동만
 *   POST /type   {"text":"..."}                 -- 키보드 입력
 *
 * 빌드:
 *   - Board: ESP32S3 Dev Module
 *   - USB Mode: USB-OTG (TinyUSB)
 *   - USB CDC On Boot: Enabled
 *   - Upload Mode: UART0 / Hardware CDC
 */

#include <WiFi.h>
#include <WebServer.h>
#include <ArduinoJson.h>
#include <USB.h>
#include <USBHIDMouse.h>
#include <USBHIDKeyboard.h>

const char* WIFI_SSID = "KT_GiGA_8650";
const char* WIFI_PASS = "1dbc4ec673";

const int PHONE_W = 1080;
const int PHONE_H = 2400;

USBHIDMouse Mouse;
USBHIDKeyboard Keyboard;
WebServer server(80);

int16_t toAbs(int pixel, int range) {
  return (int16_t)((long)pixel * 32767 / range);
}

void mouseMoveAbs(int x, int y) {
  Mouse.move(-32767, -32767);
  delay(20);
  Mouse.move(-32767, -32767);
  delay(20);
  Mouse.move(toAbs(x, PHONE_W), toAbs(y, PHONE_H));
  delay(20);
}

void handleStatus() {
  JsonDocument doc;
  doc["wifi"] = WiFi.status() == WL_CONNECTED ? "ok" : "disconnected";
  doc["ip"] = WiFi.localIP().toString();
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
  delay(2000);
  Serial.println("=== boot ===");

  // USB HID: register classes FIRST, then start USB
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
  // WiFi.setTxPower(WIFI_POWER_8_5dBm);  // USB 안정성 위해 낮췄으나 연결 실패 → 기본값 사용 (5/19)
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.println("WiFi.begin() returned, continuing setup");

  server.on("/status", HTTP_GET, handleStatus);
  server.on("/click", HTTP_POST, handleClick);
  server.on("/tap", HTTP_POST, handleTap);
  server.on("/move", HTTP_POST, handleMove);
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
    Serial.print("[status] WiFi=");
    Serial.print(WiFi.status() == WL_CONNECTED ? "OK" : "no");
    Serial.print(" IP=");
    Serial.println(WiFi.localIP());
  }
  // wifi 끊겼으면 15초마다 재연결 시도
  if (WiFi.status() != WL_CONNECTED && millis() - lastReconnect > 15000) {
    lastReconnect = millis();
    Serial.println("[reconnect] WiFi.begin() 재시도");
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASS);
  }
}

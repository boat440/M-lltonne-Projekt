#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Adafruit_NeoPixel.h>
#include <time.h>
#include "secrets.h"  // Kopie von secrets.h.example (gleicher Ordner) mit deinen Werten

// ---------------------------------------------------------------------------
// LED-Konfiguration: 8x8 WS2812-Panel an einer Datenleitung
// ---------------------------------------------------------------------------
#define LED_PIN 5                              // Datenleitung zum Panel
#define MATRIX_WIDTH 8
#define MATRIX_HEIGHT 8
#define LED_COUNT (MATRIX_WIDTH * MATRIX_HEIGHT)  // 64 LEDs

// Die meisten fertigen 8x8-WS2812-Panels sind maeanderfoermig ("serpentine")
// verdrahtet: Zeile 0 verlaeuft links->rechts, Zeile 1 rechts->links, usw.
// Falls dein Panel stattdessen jede Zeile links->rechts verdrahtet hat,
// hier auf false stellen (im Zweifel einfach ausprobieren: applyWasteDisplay()
// zeigt beim Testen sichtbar, ob die Quadranten sauber getrennt sind).
#define MATRIX_SERPENTINE true

// WICHTIG (Stromversorgung): 64 WS2812 ziehen bei voller Helligkeit und
// Weiss bis zu ~3,8A -- das schafft der 5V-Pin des ESP32-Boards NICHT.
// Panel mit eigenem 5V-Netzteil versorgen (gemeinsamen GND mit dem ESP32
// nicht vergessen!), Datenleitung nach Moeglichkeit ueber ~300-500 Ohm
// Widerstand fuehren. BRIGHTNESS unten haelt den Stromverbrauch zusaetzlich
// niedrig, ersetzt die externe Versorgung aber nicht.
#define BRIGHTNESS 40  // 0-255

Adafruit_NeoPixel strip(LED_COUNT, LED_PIN, NEO_GRB + NEO_KHZ800);

// Bildet (x, y) -- x: 0..7 von links, y: 0..7 von oben -- auf den
// tatsaechlichen Pixel-Index im Datenstrom ab.
uint16_t xyToIndex(uint8_t x, uint8_t y) {
  if (MATRIX_SERPENTINE && (y % 2 == 1)) {
    x = MATRIX_WIDTH - 1 - x;
  }
  return y * MATRIX_WIDTH + x;
}

// ---------------------------------------------------------------------------
// Anzeige-Fenster (Stunden im 24h-Format)
// ---------------------------------------------------------------------------
const int EVENING_START_HOUR = 18;  // ab 18 Uhr an (Vortag)
const int MORNING_START_HOUR = 6;   // ab 6 Uhr an (Tag der Leerung)
const int MORNING_END_HOUR = 12;    // bis 12 Uhr an (danach aus)

// Uhrzeit, zu der taeglich neu abgerufen wird
const int FETCH_HOUR = 17;
const int FETCH_MINUTE = 55;

// Wie oft im Leerlauf die Uhrzeit geprueft wird
const unsigned long CHECK_INTERVAL_MS = 30UL * 1000UL;

// ---------------------------------------------------------------------------
String currentTypesCsv = "";  // z.B. "restmuell,bio" -- zuletzt abgerufene Typen
int lastFetchedDay = -1;      // tm_yday des letzten erfolgreichen Abrufs
bool ledsCurrentlyOn = false;
unsigned long lastCheckMillis = 0;

// ---------------------------------------------------------------------------
// Anzeige: nur diese 4 Tonnenarten werden dargestellt. Steht fuer den Tag nur
// eine davon an, leuchtet das ganze Panel in ihrer Farbe. Stehen mehrere an,
// wird das Panel in gleich breite senkrechte Streifen geteilt (bei 2 Typen
// also exakt halbiert). Reihenfolge hier = Reihenfolge der Streifen von
// links nach rechts. Codes muessen zu WASTE_KEYWORDS in
// cloud-script/fetch_abfallkalender.py passen.
// ---------------------------------------------------------------------------
struct WasteDisplay {
  const char *code;
  uint32_t color;
};

const WasteDisplay WASTE_DISPLAYS[] = {
  // Restmuell "sollte" schwarz sein -- nicht darstellbar (nicht von "aus"
  // unterscheidbar), daher ein dunkles Grau als Ersatz.
  {"restmuell",   strip.Color(50, 50, 50)},  // grau
  {"papier",      strip.Color(0, 0, 90)},    // blau
  {"bio",         strip.Color(70, 35, 5)},   // braun
  {"gelber_sack", strip.Color(90, 75, 0)},   // gelb
};
const int WASTE_DISPLAY_COUNT = sizeof(WASTE_DISPLAYS) / sizeof(WASTE_DISPLAYS[0]);

void fillColumns(uint8_t colStart, uint8_t colEnd, uint32_t color) {
  for (uint8_t x = colStart; x < colEnd; x++) {
    for (uint8_t y = 0; y < MATRIX_HEIGHT; y++) {
      strip.setPixelColor(xyToIndex(x, y), color);
    }
  }
}

void applyTypesCsv(const String &csv) {
  strip.clear();

  // Erst herausfinden, welche der 4 bekannten Typen heute anstehen (in
  // fester Reihenfolge, unabhaengig davon, wie sie im JSON stehen).
  uint32_t activeColors[WASTE_DISPLAY_COUNT];
  int activeCount = 0;
  for (int i = 0; i < WASTE_DISPLAY_COUNT; i++) {
    String code = WASTE_DISPLAYS[i].code;
    bool present = false;
    int start = 0;
    while (start <= (int)csv.length()) {
      int comma = csv.indexOf(',', start);
      String type = (comma == -1) ? csv.substring(start) : csv.substring(start, comma);
      if (type == code) { present = true; break; }
      if (comma == -1) break;
      start = comma + 1;
    }
    // Andere Codes im JSON (z.B. "sperrmuell") werden hier ignoriert, da sie
    // nicht in WASTE_DISPLAYS stehen.
    if (present) activeColors[activeCount++] = WASTE_DISPLAYS[i].color;
  }

  // Dann das Panel in activeCount gleich breite senkrechte Streifen teilen.
  for (int i = 0; i < activeCount; i++) {
    uint8_t colStart = (uint8_t)((uint16_t)MATRIX_WIDTH * i / activeCount);
    uint8_t colEnd = (uint8_t)((uint16_t)MATRIX_WIDTH * (i + 1) / activeCount);
    fillColumns(colStart, colEnd, activeColors[i]);
  }

  strip.show();
}

void turnOffLeds() {
  strip.clear();
  strip.show();
}

void connectWifiIfNeeded() {
  if (WiFi.status() == WL_CONNECTED) return;
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Verbinde mit WLAN");
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
    delay(300);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("Verbunden, IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("WLAN-Verbindung fehlgeschlagen, versuche es spaeter erneut.");
  }
}

bool fetchTypesCsv(String &outCsv) {
  WiFiClientSecure client;
  client.setInsecure();  // siehe README fuer Hinweise zu TLS-Zertifikaten

  HTTPClient https;
  if (!https.begin(client, JSON_URL)) return false;

  int httpCode = https.GET();
  if (httpCode != HTTP_CODE_OK) {
    Serial.printf("HTTP-Fehler beim Abruf: %d\n", httpCode);
    https.end();
    return false;
  }

  String payload = https.getString();
  https.end();

  StaticJsonDocument<512> doc;
  if (deserializeJson(doc, payload)) {
    Serial.println("JSON konnte nicht gelesen werden.");
    return false;
  }

  String csv = "";
  for (JsonVariant v : doc["types"].as<JsonArray>()) {
    if (csv.length()) csv += ",";
    csv += v.as<String>();
  }
  outCsv = csv;

  Serial.print("Abgerufene Typen fuer ");
  Serial.print(doc["date_for"].as<const char *>());
  Serial.print(": ");
  Serial.println(csv.length() ? csv : "(keine)");
  return true;
}

bool shouldBeOn(const struct tm &now) {
  int h = now.tm_hour;
  bool eveningWindow = (h >= EVENING_START_HOUR);                         // 18-23 Uhr
  bool morningWindow = (h >= MORNING_START_HOUR && h < MORNING_END_HOUR); // 6-11 Uhr
  return eveningWindow || morningWindow;
}

void setup() {
  Serial.begin(115200);
  delay(300);

  strip.begin();
  strip.setBrightness(BRIGHTNESS);
  strip.show();  // alle LEDs aus als Startzustand

  connectWifiIfNeeded();

  // Zeitzone inkl. automatischer Sommer-/Winterzeit-Umstellung fuer Deutschland
  configTzTime("CET-1CEST,M3.5.0,M10.5.0/3", "pool.ntp.org", "de.pool.ntp.org");

  struct tm now;
  if (getLocalTime(&now, 10000)) {
    Serial.println(&now, "Zeit synchronisiert: %d.%m.%Y %H:%M:%S");
  } else {
    Serial.println("Zeit konnte nicht synchronisiert werden!");
  }
}

void loop() {
  if (millis() - lastCheckMillis < CHECK_INTERVAL_MS) {
    delay(200);
    return;
  }
  lastCheckMillis = millis();

  connectWifiIfNeeded();  // stellt die Verbindung wieder her, falls sie zwischendurch abbricht

  struct tm now;
  if (!getLocalTime(&now, 2000)) {
    Serial.println("Konnte Uhrzeit nicht lesen, ueberspringe diesen Durchlauf.");
    return;
  }

  // Einmal taeglich neue Daten holen
  bool isFetchTime = (now.tm_hour == FETCH_HOUR && now.tm_min >= FETCH_MINUTE);
  if (isFetchTime && lastFetchedDay != now.tm_yday) {
    String csv;
    if (fetchTypesCsv(csv)) {
      currentTypesCsv = csv;
      lastFetchedDay = now.tm_yday;
    }
  }

  bool wantOn = shouldBeOn(now);
  if (wantOn != ledsCurrentlyOn) {
    if (wantOn) {
      applyTypesCsv(currentTypesCsv);
      Serial.println("LEDs eingeschaltet.");
    } else {
      turnOffLeds();
      Serial.println("LEDs ausgeschaltet.");
    }
    ledsCurrentlyOn = wantOn;
  }
}

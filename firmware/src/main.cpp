#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

// ---------------------------------------------------------------------------
// Konfiguration – hier deine Werte eintragen
// ---------------------------------------------------------------------------
const char *WIFI_SSID = "DEIN_WLAN_NAME";
const char *WIFI_PASSWORD = "DEIN_WLAN_PASSWORT";

// Wird nach dem Einrichten von GitHub Pages ersetzt, siehe README.
const char *JSON_URL = "https://DEINUSERNAME.github.io/DEINREPO/abfall.json";

// Beispiel-Verkabelung: eine LED pro Tonnenart.
// Passe die Pins an deinen Aufbau an (Servos statt LEDs funktionieren
// analog, einfach in applyTypes() durch Servo.write(...) ersetzen).
const int PIN_RESTMUELL = 25;
const int PIN_GELBER_SACK = 26;
const int PIN_PAPIER = 27;
const int PIN_BIO = 14;

// Wie oft der ESP32 aus dem Deep Sleep aufwacht, um nachzuschauen.
// Einmal pro Stunde reicht dicke, da sich das JSON nur 1x täglich ändert.
const uint64_t SLEEP_SECONDS = 60ULL * 60ULL;

// ---------------------------------------------------------------------------

void connectWifi() {
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
    Serial.println("WLAN-Verbindung fehlgeschlagen!");
  }
}

void setAllOff() {
  digitalWrite(PIN_RESTMUELL, LOW);
  digitalWrite(PIN_GELBER_SACK, LOW);
  digitalWrite(PIN_PAPIER, LOW);
  digitalWrite(PIN_BIO, LOW);
}

void applyTypes(JsonArray types) {
  setAllOff();
  for (JsonVariant v : types) {
    String type = v.as<String>();
    if (type == "restmuell") digitalWrite(PIN_RESTMUELL, HIGH);
    else if (type == "gelber_sack") digitalWrite(PIN_GELBER_SACK, HIGH);
    else if (type == "papier") digitalWrite(PIN_PAPIER, HIGH);
    else if (type == "bio") digitalWrite(PIN_BIO, HIGH);
  }
}

bool fetchAndApply() {
  WiFiClientSecure client;
  // Hinweis: setInsecure() überspringt die Zertifikatsprüfung.
  // Für ein privates Hobby-Projekt üblich und unkompliziert; wer es "sauber"
  // machen möchte, hinterlegt stattdessen das Root-CA-Zertifikat von
  // github.io mit client.setCACert(...). Siehe README für Details.
  client.setInsecure();

  HTTPClient https;
  if (!https.begin(client, JSON_URL)) {
    Serial.println("HTTPS-Verbindung konnte nicht aufgebaut werden");
    return false;
  }

  int httpCode = https.GET();
  if (httpCode != HTTP_CODE_OK) {
    Serial.printf("HTTP-Fehler: %d\n", httpCode);
    https.end();
    return false;
  }

  String payload = https.getString();
  https.end();

  StaticJsonDocument<512> doc;
  DeserializationError err = deserializeJson(doc, payload);
  if (err) {
    Serial.print("JSON-Fehler: ");
    Serial.println(err.c_str());
    return false;
  }

  Serial.print("Termin für: ");
  Serial.println(doc["date_for"].as<const char *>());

  applyTypes(doc["types"].as<JsonArray>());
  return true;
}

void setup() {
  Serial.begin(115200);
  delay(500);

  pinMode(PIN_RESTMUELL, OUTPUT);
  pinMode(PIN_GELBER_SACK, OUTPUT);
  pinMode(PIN_PAPIER, OUTPUT);
  pinMode(PIN_BIO, OUTPUT);
  setAllOff();

  connectWifi();

  if (WiFi.status() == WL_CONNECTED) {
    fetchAndApply();
  }

  WiFi.disconnect(true);
  Serial.println("Gehe in Deep Sleep...");
  Serial.flush();

  esp_sleep_enable_timer_wakeup(SLEEP_SECONDS * 1000000ULL);
  esp_deep_sleep_start();
}

void loop() {
  // Wird nicht erreicht: setup() beendet sich im Deep Sleep.
}

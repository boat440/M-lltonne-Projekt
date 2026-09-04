# Mülltonne mit Anzeige – Projektübersicht

```
muelltonne-projekt/
├── .github/workflows/
│   └── update-abfallkalender.yml   # läuft täglich, holt Kalender, schreibt JSON
├── cloud-script/
│   ├── fetch_abfallkalender.py     # Python-Skript: ICS holen -> JSON schreiben
│   └── requirements.txt
├── docs/
│   └── abfall.json                 # wird automatisch überschrieben, via GitHub Pages ausgeliefert
├── firmware/                       # ESP32-Firmware (eigenes PlatformIO-Projekt)
│   ├── platformio.ini
│   └── src/main.cpp
└── README.md
```

Das Ganze sind bewusst **zwei getrennte Bereiche**, die du auch als zwei
separate VS-Code-Fenster/Ordner öffnen kannst:

- **cloud-script/** + **.github/** + **docs/** → gehört zu einem GitHub-Repository,
  läuft komplett in der Cloud (GitHub Actions), du musst dafür nichts selbst hosten.
- **firmware/** → ein eigenständiges PlatformIO-Projekt für den ESP32.

## 1. Cloud-Teil einrichten (Python + GitHub Actions + GitHub Pages)

1. Erstelle ein neues **GitHub-Repository** (öffentlich, sonst funktioniert
   GitHub Pages nicht im kostenlosen Plan) und lade den gesamten Ordner
   `muelltonne-projekt` hoch (bzw. `git init`, `git add .`, `git commit`, `git push`).
2. In den Repo-Einstellungen: **Settings → Pages → Source: Deploy from a branch →
   Branch: main, Ordner: /docs**. Speichern.
3. Nach ein paar Minuten ist deine Datei erreichbar unter:
   `https://DEINUSERNAME.github.io/DEINREPO/abfall.json`
4. Teste das Python-Skript lokal (optional, aber empfehlenswert), in VS Code:
   - Python-Extension installieren, falls nicht vorhanden.
   - Terminal in VS Code öffnen, in den Ordner `cloud-script` wechseln:
     ```bash
     cd cloud-script
     pip install -r requirements.txt
     python fetch_abfallkalender.py
     ```
   - Prüfe die Ausgabe und den Inhalt von `docs/abfall.json`.
5. In `.github/workflows/update-abfallkalender.yml` läuft das Skript danach
   automatisch jeden Tag (per `cron`) – du musst nichts weiter tun. Über den
   Reiter **Actions** im Repo kannst du es auch manuell per Klick auslösen
   ("Run workflow"), um sofort zu testen, ohne auf die Uhrzeit zu warten.

## 2. ESP32-Firmware einrichten (VS Code + PlatformIO)

1. Installiere die **PlatformIO IDE**-Extension in VS Code (Extensions-Icon →
   nach "PlatformIO IDE" suchen → installieren). VS Code startet danach neu.
2. Öffne **nur den Ordner `firmware/`** als eigenständigen Workspace in VS Code
   (`File → Open Folder…`). PlatformIO erkennt automatisch die `platformio.ini`.
3. Kopiere `firmware/src/secrets.h.example` zu `firmware/src/secrets.h` und
   trage dort ein:
   - `WIFI_SSID`, `WIFI_PASSWORD`
   - `JSON_URL` (die GitHub-Pages-URL aus Schritt 1.3)

   `secrets.h` wird per `.gitignore` nicht eingecheckt, damit dein
   WLAN-Passwort nicht im (öffentlichen) Repo landet.
4. Passe bei Bedarf die Pin-Nummern (`PIN_RESTMUELL` usw.) an deine
   tatsächliche Verkabelung an.
5. ESP32 per USB anschließen, dann in der PlatformIO-Seitenleiste (Alien-Icon
   links) auf **Upload** klicken (oder Tastenkürzel, PlatformIO zeigt es an).
6. Serial Monitor öffnen (PlatformIO-Seitenleiste → **Monitor**), um die
   Ausgaben (WLAN-Verbindung, abgerufenes JSON, evtl. Fehler) live zu sehen.

## Funktionsweise im Überblick

1. Ein GitHub-Actions-Job läuft täglich abends, ruft die Portal-API auf,
   bestimmt anhand der ICS-Daten, was **morgen** ansteht, und schreibt ein
   kleines JSON (`docs/abfall.json`).
2. GitHub Pages liefert diese Datei über eine einfache, feste HTTPS-URL aus.
3. Der ESP32 wacht regelmäßig aus dem Deep Sleep auf, holt sich per HTTPS
   dieses JSON (kein ICS-Parsing, kein Session-Handling nötig – das erledigt
   das Cloud-Skript), schaltet die passenden LEDs/Servos und schläft wieder.

## Wichtige Anpassungen für dein Setup

- **Adressdaten**: `ORTE_ID`, `STRASSEN_ID`, `HAUSNUMMER` in
  `fetch_abfallkalender.py` sind bereits mit deinen Werten aus der
  Netzwerk-Tab-URL gefüllt – prüfe sie trotzdem einmal.
- **Tonnenarten/Farben**: `WASTE_KEYWORDS` im Python-Skript und die
  `if/else`-Kette in `applyTypes()` in `main.cpp` müssen zueinander passen
  (gleiche Codes wie `"restmuell"`, `"gelber_sack"`, ...).
- **Uhrzeit des täglichen Laufs**: der `cron`-Eintrag ist in UTC – wenn du z.B.
  willst, dass die Anzeige spätestens um 19 Uhr Ortszeit steht, probiere
  verschiedene `cron`-Werte aus (denk an Sommer-/Winterzeit).

## Bekannte Stolpersteine (siehe auch vorherige Erklärung)

- Falls die Portal-API mal andere Parameter erwartet (z.B. sich `strassenId`
  ändert), einfach erneut über den Netzwerk-Tab die aktuelle URL prüfen und
  die Konstanten in `fetch_abfallkalender.py` anpassen.
- `client.setInsecure()` in der Firmware überspringt die
  TLS-Zertifikatsprüfung. Für den privaten Gebrauch unkritisch, für mehr
  Sicherheit könntest du stattdessen das Root-Zertifikat von
  `github.io` mit `client.setCACert(...)` hinterlegen.
- Wenn `docs/abfall.json` leere `types` zeigt, prüfe zuerst, ob das
  Python-Skript lokal überhaupt Termine findet (Schritt 1.4) – dann liegt es
  eher an Adressdaten/Datum als an der Firmware.

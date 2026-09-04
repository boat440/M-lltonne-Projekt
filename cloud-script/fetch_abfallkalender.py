"""
Ruft den Abfallkalender für eine feste Adresse in Oberursel ab, bestimmt
die für MORGEN anstehende(n) Abholung(en) und schreibt das Ergebnis als
kompaktes JSON nach docs/abfall.json. Diese Datei wird per GitHub Pages
ausgeliefert und vom ESP32 abgefragt.

Läuft lokal mit:  python fetch_abfallkalender.py
Läuft automatisiert über den GitHub-Actions-Workflow (siehe .github/workflows).
"""

import base64
import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
from icalendar import Calendar

# --- Konfiguration: Adressdaten -------------------------------------------
# ORTE_ID/STRASSEN_ID/HAUSNUMMER stehen NICHT im Code (das waere im
# oeffentlichen Repo einsehbar und wuerde die Adresse verraten -- jede:r
# koennte damit dieselbe Abfrage beim Portal stellen).
#
# - Lokal: cloud-script/local_config.py anlegen (siehe
#   local_config.py.example), wird per .gitignore nicht committet.
# - In GitHub Actions: als Repository Secrets hinterlegt und vom Workflow
#   als Umgebungsvariablen hereingereicht (siehe README).
try:
    from local_config import ORTE_ID, STRASSEN_ID, HAUSNUMMER  # type: ignore
except ImportError:
    ORTE_ID = os.environ.get("ORTE_ID")
    STRASSEN_ID = os.environ.get("STRASSEN_ID")
    HAUSNUMMER = os.environ.get("HAUSNUMMER")

if not all([ORTE_ID, STRASSEN_ID, HAUSNUMMER]):
    sys.exit(
        "Fehlende Adressdaten: ORTE_ID/STRASSEN_ID/HAUSNUMMER sind nicht gesetzt.\n"
        "Lokal: cloud-script/local_config.py aus local_config.py.example erstellen.\n"
        "GitHub Actions: Repository Secrets ORTE_ID/STRASSEN_ID/HAUSNUMMER anlegen (siehe README)."
    )

BASE_URL = "https://buerger-portal-oberursel.azurewebsites.net/api/ZeigeAbfallkalender"
TIMEZONE = ZoneInfo("Europe/Berlin")

# Diese Stichworte tauchen im Kalender auf, sind aber KEINE echten
# Abholtermine, sondern nur Hinweise (z.B. Feiertag, Wertstoffhof
# geschlossen) -> werden komplett ignoriert, statt als "sonstiges" gezählt.
IGNORE_KEYWORDS = ["feiertag", "geschlossen"]

# Zuordnung: Stichwort im Kalendereintrag (klein geschrieben) -> interner Code.
# Diesen Code benutzt später die ESP32-Firmware, um die passende LED zu schalten.
# Die Stichworte sind an die tatsächlichen Bezeichnungen im Oberursel-Kalender
# angepasst (z.B. "Restabfall" statt "Restmüll", "Gelber Sack" statt "Gelbe Tonne").
WASTE_KEYWORDS = {
    "bioabfall": "bio",
    "restabfall": "restmuell",
    "gelber sack": "gelber_sack",
    "leichtverpackungen": "gelber_sack",
    "papier": "papier",
    "schadstoffmobil": "schadstoff",
    "weihnacht": "weihnachtsbaum",   # deckt "Weihnachtsbaum" UND "Weihnachtsbäume" ab
    "grün": "gruenschnitt",
    "sperr": "sperrmuell",
}


PORTAL_ORIGIN = "https://buerger-portal-oberursel.azurewebsites.net"

# Diese Header sind 1:1 aus einem funktionierenden Browser-Request übernommen
# (Chrome DevTools -> Network -> Copy as cURL). Der entscheidende Header ist
# "Accept: ... text/calendar" -- ohne den liefert der Server eine
# PDF-Variante statt des echten ICS-Inhalts zurück.
HEADERS = {
    "Accept": "application/json, text/plain;q=0.5, text/calendar",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": PORTAL_ORIGIN,
    "Referer": f"{PORTAL_ORIGIN}/calendar",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

# Wird im Browser als einfaches, unkritisches Sprach-Cookie mitgeschickt.
COOKIES = {"i18n_redirected": "de"}


def build_url(year: int) -> str:
    """Baut die Kalender-URL exakt nach dem Muster, das das Portal selbst nutzt."""
    hausnr = quote(f"'{HAUSNUMMER}'", safe="")
    dateiname = quote(f"'Abfallkalender{year}.ics'", safe="")
    return (
        f"{BASE_URL}?orteId={ORTE_ID}&strassenId={STRASSEN_ID}"
        f"&hausNr={hausnr}&dateiName={dateiname}"
        f"&unixZeitOption=-25200&fixedYear={year}"
    )


def _search_json_for_ics(obj) -> str | None:
    """Sucht rekursiv in einer JSON-Struktur nach einem String, der ICS-Inhalt enthält."""
    if isinstance(obj, str) and "BEGIN:VCALENDAR" in obj:
        return obj
    if isinstance(obj, dict):
        for value in obj.values():
            result = _search_json_for_ics(value)
            if result:
                return result
    if isinstance(obj, list):
        for item in obj:
            result = _search_json_for_ics(item)
            if result:
                return result
    return None


def _find_json_key(obj, key: str):
    """Sucht rekursiv in einer JSON-Struktur nach dem ersten Wert für 'key'."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            result = _find_json_key(value, key)
            if result is not None:
                return result
    if isinstance(obj, list):
        for item in obj:
            result = _find_json_key(item, key)
            if result is not None:
                return result
    return None


def _decode_base64_ics(candidate: str) -> bytes | None:
    """Dekodiert einen Base64-String und prüft, ob echter ICS-Inhalt rauskommt."""
    try:
        decoded = base64.b64decode(candidate)
    except (ValueError, TypeError):
        return None
    if decoded.lstrip().startswith(b"BEGIN:VCALENDAR"):
        return decoded
    return None


def extract_ics_content(raw: bytes, year: int) -> bytes | None:
    """
    Der Server liefert nicht immer reines ICS. Beobachtete Varianten:
    - direkt reiner ICS-Text
    - JSON-Hülle mit einem Feld "FileContents", das den ICS-Text Base64-kodiert enthält
    - XML-Hülle mit einem <FileContents>-Element, ebenfalls Base64-kodiert
    (Enthält "FileContents" stattdessen ein PDF, ist die Kalenderdatei für diesen
    Abruf einfach nicht als ICS verfügbar -> wird als "nicht gefunden" behandelt.)
    Gelingt keine der Varianten, wird die Rohantwort zur Fehlersuche in eine
    Debug-Datei geschrieben.
    """
    text = raw.decode("utf-8", errors="replace")
    stripped = text.lstrip()

    # Fall 1: schon reines ICS
    if stripped.startswith("BEGIN:VCALENDAR"):
        return raw

    # Fall 2: JSON-Hülle, z.B. {"d": {"ZeigeAbfallkalender": {"FileContents": "<base64>", ...}}}
    try:
        data = json.loads(text)
        candidate = _find_json_key(data, "FileContents")
        if isinstance(candidate, str):
            decoded = _decode_base64_ics(candidate)
            if decoded:
                return decoded
        # Falls der ICS-Text ausnahmsweise unkodiert irgendwo im JSON steckt
        found = _search_json_for_ics(data)
        if found:
            return found.encode("utf-8")
    except json.JSONDecodeError:
        pass

    # Fall 3: XML-Hülle, gleiches Prinzip: <FileContents>-Knoten suchen & dekodieren
    if stripped.startswith("<"):
        try:
            root = ET.fromstring(text)
            for elem in root.iter():
                tag = elem.tag.split("}")[-1]  # Namespace-Präfix entfernen
                if tag == "FileContents" and elem.text:
                    decoded = _decode_base64_ics(elem.text.strip())
                    if decoded:
                        return decoded
                if elem.text and "BEGIN:VCALENDAR" in elem.text:
                    return elem.text.encode("utf-8")
        except ET.ParseError:
            pass

    # Nichts gefunden -> Rohantwort zur Analyse speichern
    debug_path = Path(__file__).resolve().parent / f"debug_response_{year}.txt"
    debug_path.write_text(text, encoding="utf-8")
    print(
        f"Konnte keinen ICS-Inhalt in der Antwort für {year} finden "
        f"(evtl. war die Antwort diesmal eine PDF-Variante). "
        f"Rohantwort gespeichert unter: {debug_path}"
    )
    return None


def fetch_ics(year: int) -> bytes | None:
    url = build_url(year)
    try:
        # POST mit leerem Body, genau wie im Browser (Content-Length: 0).
        resp = requests.post(url, headers=HEADERS, cookies=COOKIES, data="", timeout=20)
        resp.raise_for_status()
        return extract_ics_content(resp.content, year)
    except requests.RequestException as exc:
        print(f"Warnung: Abruf für Jahr {year} fehlgeschlagen ({exc})")
        return None


def classify(summary: str) -> str | None:
    """Ordnet einen Kalendereintrag einer Tonnenart zu.

    Gibt None zurück, wenn es sich um einen reinen Info-Eintrag handelt
    (z.B. Feiertagshinweis), der nicht als Abholtermin zählen soll.
    """
    lowered = summary.lower()
    if any(keyword in lowered for keyword in IGNORE_KEYWORDS):
        return None
    for keyword, code in WASTE_KEYWORDS.items():
        if keyword in lowered:
            return code
    return "sonstiges"


def collect_events_for(target_day: date, years_to_try: list[int]) -> list[str]:
    """Sucht in den ICS-Dateien der gegebenen Jahre nach Terminen an target_day."""
    found: dict[str, str] = {}
    for year in years_to_try:
        ics_bytes = fetch_ics(year)
        if ics_bytes is None:
            continue
        try:
            cal = Calendar.from_ical(ics_bytes)
        except ValueError as exc:
            print(f"Warnung: ICS für Jahr {year} konnte nicht geparst werden ({exc})")
            continue
        for component in cal.walk("VEVENT"):
            dtstart = component.get("dtstart").dt
            if isinstance(dtstart, datetime):
                dtstart = dtstart.date()
            if dtstart == target_day:
                summary = str(component.get("summary"))
                code = classify(summary)
                if code is not None:
                    found[code] = summary
    return sorted(found.keys())


def main() -> None:
    now = datetime.now(TIMEZONE)
    tomorrow = (now + timedelta(days=1)).date()
    
    # Um den Jahreswechsel herum kann "morgen" schon im neuen Kalenderjahr
    # liegen, dessen ICS-Datei ggf. schon existiert -> beide Jahre probieren.
    years_to_try = sorted({tomorrow.year, now.year})

    types = collect_events_for(tomorrow, years_to_try)

    payload = {
        "generated_at": now.isoformat(timespec="seconds"),
        "date_for": tomorrow.isoformat(),
        "types": types,
    }

    out_path = Path(__file__).resolve().parent.parent / "docs" / "abfall.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Geschrieben nach {out_path}:")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

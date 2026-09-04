"""
Ruft den Abfallkalender für eine feste Adresse in Oberursel ab, bestimmt
die für MORGEN anstehende(n) Abholung(en) und schreibt das Ergebnis als
kompaktes JSON nach docs/abfall.json. Diese Datei wird per GitHub Pages
ausgeliefert und vom ESP32 abgefragt.

Läuft lokal mit:  python fetch_abfallkalender.py
Läuft automatisiert über den GitHub-Actions-Workflow (siehe .github/workflows).
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
from icalendar import Calendar

# --- Konfiguration: hier deine Adressdaten eintragen -----------------------
# Diese Werte stehen in der URL, die du im Netzwerk-Tab deines Browsers
# gesehen hast (orteId, strassenId, hausNr).
ORTE_ID = "REDACTED"
STRASSEN_ID = "REDACTED"
HAUSNUMMER = "REDACTED"

BASE_URL = "https://buerger-portal-oberursel.azurewebsites.net/api/ZeigeAbfallkalender"
TIMEZONE = ZoneInfo("Europe/Berlin")

# Zuordnung: Stichwort im Kalendereintrag (klein geschrieben) -> interner Code.
# Diesen Code benutzt später die ESP32-Firmware, um die passende LED zu schalten.
WASTE_KEYWORDS = {
    "restm": "restmuell",
    "gelbe": "gelber_sack",
    "gelber": "gelber_sack",
    "papier": "papier",
    "bio": "bio",
    "sperr": "sperrmuell",
    "schadstoff": "schadstoff",
    "weihnachtsbaum": "weihnachtsbaum",
}


def build_url(year: int) -> str:
    """Baut die Kalender-URL exakt nach dem Muster, das das Portal selbst nutzt."""
    hausnr = quote(f"'{HAUSNUMMER}'", safe="")
    dateiname = quote(f"'Abfallkalender{year}.ics'", safe="")
    return (
        f"{BASE_URL}?orteId={ORTE_ID}&strassenId={STRASSEN_ID}"
        f"&hausNr={hausnr}&dateiName={dateiname}"
        f"&unixZeitOption=-25200&fixedYear={year}"
    )


def fetch_ics(year: int) -> bytes | None:
    url = build_url(year)
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        return resp.content
    except requests.RequestException as exc:
        print(f"Warnung: Abruf für Jahr {year} fehlgeschlagen ({exc})")
        return None


def classify(summary: str) -> str:
    lowered = summary.lower()
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
        cal = Calendar.from_ical(ics_bytes)
        for component in cal.walk("VEVENT"):
            dtstart = component.get("dtstart").dt
            if isinstance(dtstart, datetime):
                dtstart = dtstart.date()
            if dtstart == target_day:
                summary = str(component.get("summary"))
                found[classify(summary)] = summary
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

# Hausfinder (Kreuzlingen/Gottlieben/Taegerwilen)

Automatischer täglicher Immobilien-Scan (Kauf + Miete), mit Dashboard statt E-Mail und historischer Datensammlung für ein Preis-Modell ("eher teuer" vs. "eher günstig").

## Aktueller Scope

- Orte strikt: Kreuzlingen, Gottlieben, Taegerwilen (inkl. Postcodes 8280/8274).
- Preislimits und Distanz-Referenzpunkt sind konfigurierbar (siehe "Öffentliches Repo & Privatsphäre").
- Quellenreihenfolge: zuerst Makler, dann Portale.

## Was der Runner macht

- Liest Quellen aus `config/config.yaml`.
- Extrahiert Inseratslinks heuristisch und parst Preis/Flaeche/Zimmer/Adresse.
- Filtert nach Familienkriterien.
- Schreibt alle gefilterten Treffer historisch nach SQLite (`listings_history`).
- Unterdrueckt doppelte Benachrichtigungen (`seen_listings`).
- Rendert ein lokales Dashboard nach `data/dashboard.html`.
- Oeffnet das Dashboard im Browser (konfigurierbar).

## Schnellstart

```bash
cd haus_project
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
cp config/config.example.yaml config/config.yaml
python3 -m hausfinder.main --config config/config.yaml --dry-run
```

## Vercel Homepage

Die statische Homepage liegt direkt im Repo-Root (`index.html`, `assets/`).
Sie zeigt Kauf- und Mietobjekte mit mindestens 5 Zimmern und mindestens 100 qm,
sortiert nach Distanz zum konfigurierten Referenzpunkt (Default: Kreuzlingen Zentrum). Die Kaufansicht
liegt auf `/`, die Mietansicht auf `/mieten`.
Die Kartenansicht nutzt Leaflet und OpenStreetMap-Kacheln. Beim Öffnen der Seite
werden dadurch Browser-Requests an `unpkg.com` und `tile.openstreetmap.org`
gesendet.

Lokale Vorschau:

```bash
PYTHONPATH=src python3 scripts/export_vercel_data.py --source live --output assets/listings.json
python3 -m http.server 4173
```

Danach im Browser `http://127.0.0.1:4173` öffnen.

Einzelne Detailseiten, die nicht von einer Anbieter-Übersicht verlinkt werden,
können beim Export zusätzlich erzwungen werden:

```bash
PYTHONPATH=src python3 scripts/export_vercel_data.py --source live --extra-url "https://example.com/listing" --output assets/listings.json
```

## Täglicher Lauf (19:00, macOS)

Der Daily-Job aktualisiert lokal die SQLite-Historie und das Dashboard. Danach
erzeugt er `assets/listings.json` neu. Falls sich diese Datei geändert hat,
committed und pusht er sie nach GitHub; Vercel deployt dann automatisch neu.

1. `scripts/com.hausfinder.daily.plist.example` anpassen.
2. Als `~/Library/LaunchAgents/com.hausfinder.daily.plist` speichern.
3. Laden:

```bash
launchctl unload ~/Library/LaunchAgents/com.hausfinder.daily.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.hausfinder.daily.plist
launchctl list | grep hausfinder
```

## Täglicher Lauf mit GitHub Actions + Supabase

Der robustere Zielpfad ist:

1. GitHub Actions startet täglich.
2. Der Scraper schreibt die Historie nach Supabase Postgres.
3. Der Export erzeugt `assets/listings.json`.
4. Nur bei geänderter JSON-Datei committed die Action nach GitHub.
5. Vercel deployt den Commit über die GitHub-Integration.

Setup:

```bash
cp .env.example .env
# SUPABASE_DB_URL in .env ausfüllen
set -a
source .env
set +a
PYTHONPATH=src python3 scripts/migrate_sqlite_to_supabase.py
```

In GitHub muss zusätzlich das Repository Secret `SUPABASE_DB_URL` gesetzt sein.
Details stehen in `docs/supabase_scraping.md`.

Optional: `NTFY_TOPIC` in `.env` (und als GitHub-Secret) aktiviert
Push-Benachrichtigungen via [ntfy.sh](https://ntfy.sh), wenn ein Scrape-Lauf
neue Listings findet — ohne Topic wird der Versand einfach übersprungen.
Achtung: Der Fingerprint enthält den Preis, d.h. eine Preisänderung meldet
ein bestehendes Inserat erneut (gewollter Preis-Alert).

## Modell-Workflow (historische SQL-Daten)

Der ML-Teil ist optional und derzeit nur eine lokale Baseline. Er entscheidet
nicht, was auf der Vercel-Homepage erscheint. Die Homepage nutzt aktuell harte
Regeln: Kauf, mindestens 5 Zimmer, mindestens 100 qm, Default-Preisfilter bis
zum konfigurierten Preislimit (Default: CHF 3 Mio.).

### 1. Daten sammeln

- Lass den Daily-Runner laufen, damit `listings_history` wächst.
- Erst mit nennbarer Historie trainieren. Das Skript braucht mindestens ca. 30
  gelabelte Samples; mehr ist besser, weil die Daten aus Web-Scraping noisig sind.

### 2. ML-Dependencies installieren

```bash
.venv/bin/python3 -m pip install -e ".[ml]"
```

### 3. Trainingsdaten exportieren + Baseline trainieren

```bash
PYTHONPATH=src .venv/bin/python3 scripts/train_price_model.py --db-path data/seen.sqlite3
```

Outputs:
- `data/model/training_rows.csv`
- `data/model/price_classifier.joblib`
- `data/model/metrics.json`

Label-Logik:
- "günstig" falls CHF/qm <= 0.92 * lokaler Median (pro Kategorie + Ort)
- "teuer" falls CHF/qm >= 1.08 * lokaler Median
- Dazwischen wird für das Training ausgelassen (stabilere Klassen)

## Öffentliches Repo & Privatsphäre

Dieses Repo ist für die Veröffentlichung gedacht. Deshalb gilt:

- **Persönliche Werte gehören nicht ins Repo.** Distanz-Referenzpunkt und
  Preislimits sind neutral voreingestellt (Kreuzlingen Zentrum, CHF 3 Mio. /
  CHF 5'000) und nur über Umgebungsvariablen überschreibbar:
  `HAUS_REF_CH_LABEL`, `HAUS_REF_CH_LAT`, `HAUS_REF_CH_LON`,
  `HAUS_BUY_LIMIT_CHF`, `HAUS_RENT_LIMIT_CHF`, `HAUS_SITE_URL` (ntfy-Klick-Ziel).
- **Nicht in GitHub Actions setzen:** Die Action committed `assets/listings.json`
  ins Repo. Alles, was dort als Env-Variable gesetzt ist, steht danach öffentlich
  in dieser Datei (Referenzpunkt, Limits, daraus berechnete Distanzen). Die
  Overrides gelten nur für lokale oder private Läufe.
- **Secrets** (`SUPABASE_DB_URL`, `NTFY_TOPIC`) nur als GitHub-Secrets bzw. in der
  lokalen `.env` (gitignored).
- **Vercel-Deployment schützen:** Der Datenstand ist öffentlich im Repo, die
  Seite selbst muss es nicht sein. In den Vercel-Projekteinstellungen unter
  *Deployment Protection* die Vercel-Authentifizierung für *alle* Deployments
  aktivieren (der Standardmodus lässt die Produktions-URL offen).
- `tests/test_public_hygiene.py` schlägt fehl, wenn persönliche Muster
  (Privatadressen, lokale Pfade, private E-Mail-Adressen) in versionierte
  Dateien geraten.

## Datenquellen & Lizenzen

- Kartendaten und die daraus abgeleiteten Dateien `assets/schools.geojson` und
  `assets/nl_streets.geojson`: © OpenStreetMap-Mitwirkende, [ODbL](https://www.openstreetmap.org/copyright).
- Inseratsdaten stammen von den öffentlichen Seiten der jeweiligen Anbieter; die
  Rechte an den Inhalten verbleiben bei diesen. Es werden nur strukturierte
  Eckdaten und Links gespeichert, keine Bilder oder Volltexte.

## Sicherheits-Hinweise

- Das Skript macht aktive HTTP-Requests zu Portalen/Maklerseiten (Internet).
- Dashboard ist lokal (`file://`), kein externer Upload.
- Falls du spaeter externe APIs (z.B. Geocoding) einbindest, explizit kennzeichnen.
- `scikit-learn` ist zusaetzliche Abhaengigkeit (Supply-Chain-Risiko klein, aber vorhanden): Version pinnen und regelmaessig aktualisieren.

## Lernwert / Architektur

- Batch-Idempotenz: `seen_listings` verhindert doppelte Alerts.
- Historisierung: `listings_history` trennt Beobachtung von Entscheidung.
- Explizite Feature-Pipeline: SQL -> Features -> Labels -> Modell.
- Robustheitsprinzip: Source-Fehler (429/Timeout) stoppen den Gesamtjob nicht.

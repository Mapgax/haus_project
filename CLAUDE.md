# Project_Haus — CLAUDE.md

Property-search scraper + map (RealAdvisor listings, school-catchment overlays for
Kreuzlingen/Tägerwilen/Bottighofen). **This directory is the git root and the Vercel project root.**
Deploy: Vercel, static site reading `assets/listings.json`.

## Runbook

- Re-scrape and refresh the DB (the standard, frequently-repeated request):
  ```
  set -a && source .env && set +a && PYTHONPATH=src .venv/bin/python3 \
    scripts/run_scrape_supabase.py --config config/config.yaml --output assets/listings.json
  ```
- Tests: `.venv/bin/python3 -m pytest`.
- Secrets: `.env` → `SUPABASE_DB_URL`, `DATABASE_URL`, `NTFY_TOPIC` (ntfy.sh-Topic
  für Push bei neuen Findings; auch als GitHub-Actions-Secret. Fehlt es, wird der
  Versand übersprungen. Lokale Läufe pushen echte Notifications — zum Testen
  `--notify-dry-run` anhängen).

## The daily scraper Action — read this before pushing

`.github/workflows/scrape-listings.yml` runs daily at 17:17 UTC, re-scrapes, and — if
`assets/listings.json` changed — commits and pushes straight to `main` as
`github-actions[bot]`. **This has caused a push/rebase conflict on `assets/listings.json`
in every session that pushed** (`! [rejected] main -> main (fetch first)`, plus at least
one full merge conflict and one `HEAD.lock` error). `assets/listings.json` is a fully
regenerated artifact — never hand-merge it. On conflict: `git fetch`, rebase, and for any
conflict in `assets/listings.json` take the incoming (Action's) version
(`git checkout --theirs assets/listings.json && git add assets/listings.json`) or just
re-run the scraper locally and regenerate it, then continue the rebase. The `/ship` skill
does this automatically.

## Scraper sources — audit findings (2026-07-05)

- **Homegate, ImmoScout24, newhome, Comparis are bot-blocked (HTTP 403, even with a
  browser UA)** — they are `enabled: false` in the config with comments; don't
  re-enable without re-probing. ImmoScout24 inventory is captured anyway via
  **immostreet.ch** (an SMG satellite): its result pages are server-rendered
  `article.results-item` cards with price, rooms, area, and exact coordinates;
  `sources.py::_extract_immostreet_cards` parses them directly because the detail
  links point at the blocked immoscout24.ch.
- `config/config.yaml` is gitignored; **CI copies `config/config.example.yaml` over
  it** — every config change must go into the example file too (keep them identical).
- Sources with `url_buy == url_rent` are fetched once and the category is inferred
  per listing (`_infer_category`) — don't add duplicate buy/rent passes for those.
- `listings_history` upserts on `fp` (refreshes address/raw_text/fetched_at); the
  export keeps only the **newest row per URL** (`_latest_rows`), so stale bad parses
  age out on the next scrape instead of persisting forever. Zusätzlich fliegen Rows
  raus, die der aktuelle Lauf nicht aufgefrischt hat (`_fresh_rows`, 20h) — sonst
  taucht ein an der Quelle gelöschtes Objekt wieder auf, sobald sein Dedupe-Zwilling
  wegfällt (passiert am 2026-07-15 mit immoscout24 4003261712).
- Die Verkauft-OCR macht zuerst einen Grauwert-Pass mit `--psm 6` (kontrastarme
  Banner: braune Schrift auf cremefarbenem Kasten überleben
  den harten 140er-Schwellwert nicht), danach die Schwellwert-Rotationen für
  diagonale Banner. Regression: `tests/fixtures/sold_banner_synthetic.jpg` (synthetisch erzeugt)
  (Test läuft nur mit installiertem tesseract). Für künftige OCR-Misses gibt es
  `MANUALLY_DELISTED_URLS` in `export_vercel_data.py` (aktuell leer) als
  Notausschalter. Pagination-Links (`/page/N`) werden weder gescrapt noch
  exportiert — ein Page-2-Link war mal ein falscher Bottighofen-Pin.
- Broker footer contact blocks ("kauf@…, Hafenstrasse 48, 8280 Kreuzlingen") must
  never count as a property address — that once put a Fruthwilen house on the
  Kreuzlingen map. `_clean_address` empties addresses containing `@`/`+41`.

## Verifying instead of pasting

Check the deployed site directly instead of asking the owner to eyeball it: Vercel MCP
(`web_fetch_vercel_url` / `list_deployments`). The deployment is access-protected.

## Preferences

- Map/boundary work: use actual streets/regions, not eyeballed/ruler-straight lines —
  this was rejected twice in one session for exactly that reason.
- "Inserat anzeigen" deep links must point at the concrete RealAdvisor listing, not a
  generic category page. Geocoding must resolve to the stated street address, not the
  town center.
- Bias to action once reviewed: "alles direkt implementieren" is the default after a
  `/code-review` or `/bad-day-reviewer` pass.

## Context hygiene

School-zone/geo work (Overpass API dumps, full GeoJSON rewrites) is what blew one session
to 8.3MB and forced a mid-task compaction. Write bulk API/geo output to a file and edit
GeoJSON incrementally rather than reprinting whole files into the conversation.

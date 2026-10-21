from __future__ import annotations

import argparse
from collections import Counter
import os
from pathlib import Path

from hausfinder.config import load_config
from hausfinder.filtering import apply_filters
from hausfinder.imagecheck import check_title_image
from hausfinder.notify import send_new_listing_notifications
from hausfinder.postgres_store import PostgresSeenStore
from hausfinder.sources import fetch_all_sources

from export_vercel_data import (
    DEFAULT_EXTRA_URLS,
    MAX_ROW_AGE_HOURS,
    _load_extra_urls,
    export_data,
)


def _run_image_checks(filtered: list, user_agent: str) -> None:
    """OCR buy-listing title images for sold banners (see imagecheck.py).

    ~20-60 images per run — small enough to re-check on every scrape, which
    is what lets a listing transition to sold later. Identical image URLs
    (same object via several passes) are only checked once per run.
    """
    verdicts: dict[str, tuple[str, str | None]] = {}
    checked = sold = errors = 0
    for item in filtered:
        if item.category != "buy" or not item.image_url:
            continue
        if item.image_url not in verdicts:
            verdicts[item.image_url] = check_title_image(
                item.image_url, user_agent=user_agent
            )
            checked += 1
        status, keyword = verdicts[item.image_url]
        item.image_status = status
        if status == "sold":
            sold += 1
            print(f"[imagecheck] sold banner ({keyword}): {item.url}")
        elif status == "error":
            errors += 1
    print(f"Image checks: {checked} checked, {sold} sold, {errors} errors")


def _database_url(value: str | None) -> str:
    url = value or os.environ.get("SUPABASE_DB_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit(
            "Missing database URL. Set SUPABASE_DB_URL or pass --database-url."
        )
    return url


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scrape listings, persist them in Supabase, and export Vercel data."
    )
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--database-url")
    parser.add_argument("--schema", default="db/schema.sql")
    parser.add_argument("--apply-schema", action="store_true")
    parser.add_argument("--output", default="assets/listings.json")
    parser.add_argument(
        "--extra-url",
        action="append",
        default=[],
        help="Additional known buy listing detail URL for the static homepage export.",
    )
    parser.add_argument(
        "--notify-dry-run",
        action="store_true",
        help="Print ntfy payloads for new listings instead of sending them.",
    )
    args = parser.parse_args()

    database_url = _database_url(args.database_url)
    config = load_config(args.config)
    raw = fetch_all_sources(config)
    filtered = apply_filters(raw, config.filters, config.filters_nl)

    raw_counts = Counter(item.source_name for item in raw)
    kept_counts = Counter(item.source_name for item in filtered)
    print("Per-source scrape results (raw -> kept):")
    for name in sorted(raw_counts):
        print(f"  {name}: {raw_counts[name]} -> {kept_counts.get(name, 0)}")

    # Ohne diesen Abbruch würde ein Totalausfall aller Quellen (Runner ohne
    # Netz, global geblockter UA) jede History-Row über den Frische-Cutoff
    # altern lassen und eine leere Karte veröffentlichen.
    if not filtered:
        raise SystemExit(
            "Scrape lieferte 0 Listings — Export abgebrochen, Karte bleibt unverändert."
        )

    _run_image_checks(filtered, config.user_agent)

    store = PostgresSeenStore(database_url)
    try:
        if args.apply_schema:
            store.apply_schema(args.schema)
        store.record_history(filtered)
        new_items = store.filter_new(filtered)
        rows = store.load_history()
    finally:
        store.close()

    # Sold-flagged listings are already marked seen above (intentional: if
    # the OCR was a false positive, the listing still reaches the site once
    # the verdict clears — it just never pushes a notification).
    new_items = [item for item in new_items if item.image_status != "sold"]

    # Notify before the export: the fingerprints above are already committed
    # as seen, so if the export crashed first these alerts would be lost for
    # good on the next run (delivery is at-most-once by design).
    send_new_listing_notifications(
        new_items, config.ntfy, dry_run=args.notify_dry_run
    )

    extra_urls = [
        *DEFAULT_EXTRA_URLS,
        *(("buy", url) for url in args.extra_url),
    ]
    rows.extend(_load_extra_urls(extra_urls))
    export_data(
        rows, Path(args.output), "supabase", max_row_age_hours=MAX_ROW_AGE_HOURS
    )
    print(f"Scraped filtered listings: {len(filtered)}")
    print(f"New listings: {len(new_items)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

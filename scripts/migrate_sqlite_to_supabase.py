from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3

from hausfinder.postgres_store import PostgresSeenStore


def _database_url(value: str | None) -> str:
    url = value or os.environ.get("SUPABASE_DB_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit(
            "Missing database URL. Set SUPABASE_DB_URL or pass --database-url."
        )
    return url


def _sqlite_rows(db_path: Path, table: str) -> list[dict[str, object]]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]


def _insert_seen(store: PostgresSeenStore, rows: list[dict[str, object]]) -> int:
    if not rows:
        return 0
    with store.conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO public.seen_listings (fp, url, title, first_seen_utc)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (fp) DO NOTHING
            """,
            [
                (row["fp"], row["url"], row["title"], row["first_seen_utc"])
                for row in rows
            ],
        )
    store.conn.commit()
    return len(rows)


def _insert_history(store: PostgresSeenStore, rows: list[dict[str, object]]) -> int:
    if not rows:
        return 0
    with store.conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO public.listings_history (
                fp, fetched_at_utc, source_name, category, title, url, address,
                price_chf, area_sqm, rooms, score, raw_text,
                image_url, image_status, region, currency
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (fp) DO NOTHING
            """,
            [
                (
                    row["fp"],
                    row["fetched_at_utc"],
                    row["source_name"],
                    row["category"],
                    row["title"],
                    row["url"],
                    row["address"],
                    row["price_chf"],
                    row["area_sqm"],
                    row["rooms"],
                    row["score"],
                    row["raw_text"],
                    row.get("image_url"),
                    row.get("image_status"),
                    row.get("region") or "ch",
                    row.get("currency") or "CHF",
                )
                for row in rows
            ],
        )
    store.conn.commit()
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate local Hausfinder SQLite data into Supabase Postgres."
    )
    parser.add_argument("--sqlite-path", default="data/seen.sqlite3")
    parser.add_argument("--database-url")
    parser.add_argument("--schema", default="db/schema.sql")
    parser.add_argument("--skip-schema", action="store_true")
    args = parser.parse_args()

    sqlite_path = Path(args.sqlite_path)
    if not sqlite_path.exists():
        raise SystemExit(f"SQLite database not found: {sqlite_path}")

    store = PostgresSeenStore(_database_url(args.database_url))
    try:
        if not args.skip_schema:
            store.apply_schema(args.schema)
        seen_count = _insert_seen(store, _sqlite_rows(sqlite_path, "seen_listings"))
        history_count = _insert_history(
            store,
            _sqlite_rows(sqlite_path, "listings_history"),
        )
    finally:
        store.close()

    print(f"Migrated seen_listings rows: {seen_count}")
    print(f"Migrated listings_history rows: {history_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

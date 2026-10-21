from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from .models import Listing


def _fingerprint(listing: Listing) -> str:
    raw = "|".join(
        [
            listing.category,
            listing.url.strip().lower(),
            (listing.title or "").strip().lower(),
            str(listing.price_chf or ""),
            str(listing.area_sqm or ""),
            str(listing.rooms or ""),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class SeenStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_listings (
                fp TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                title TEXT NOT NULL,
                first_seen_utc TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS listings_history (
                fp TEXT PRIMARY KEY,
                fetched_at_utc TEXT NOT NULL,
                source_name TEXT NOT NULL,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                address TEXT NOT NULL,
                price_chf REAL,
                area_sqm REAL,
                rooms REAL,
                score INTEGER NOT NULL,
                raw_text TEXT NOT NULL,
                image_url TEXT,
                image_status TEXT,
                region TEXT NOT NULL DEFAULT 'ch',
                currency TEXT NOT NULL DEFAULT 'CHF'
            )
            """
        )
        self._ensure_columns()
        self.conn.commit()

    def _ensure_columns(self) -> None:
        # SQLite has no ADD COLUMN IF NOT EXISTS; migrate pre-existing local
        # databases by adding whatever the CREATE TABLE above already gives
        # fresh installs.
        existing = {
            row[1]
            for row in self.conn.execute("PRAGMA table_info(listings_history)")
        }
        for name, ddl in (
            ("image_url", "image_url TEXT"),
            ("image_status", "image_status TEXT"),
            ("region", "region TEXT NOT NULL DEFAULT 'ch'"),
            ("currency", "currency TEXT NOT NULL DEFAULT 'CHF'"),
        ):
            if name not in existing:
                self.conn.execute(f"ALTER TABLE listings_history ADD COLUMN {ddl}")

    def record_history(self, listings: list[Listing]) -> None:
        for item in listings:
            fp = _fingerprint(item)
            self.conn.execute(
                """
                INSERT INTO listings_history (
                    fp, fetched_at_utc, source_name, category, title, url, address,
                    price_chf, area_sqm, rooms, score, raw_text,
                    image_url, image_status, region, currency
                ) VALUES (?, datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fp) DO UPDATE SET
                    fetched_at_utc = datetime('now'),
                    address = excluded.address,
                    score = excluded.score,
                    raw_text = excluded.raw_text,
                    image_url = excluded.image_url,
                    -- Mirror of the Postgres store: 'error' never overwrites
                    -- 'sold', a genuine 'clear' does.
                    image_status = CASE
                        WHEN excluded.image_status = 'error'
                             AND listings_history.image_status = 'sold'
                        THEN 'sold'
                        ELSE excluded.image_status
                    END,
                    region = excluded.region,
                    currency = excluded.currency
                """,
                (
                    fp,
                    item.source_name,
                    item.category,
                    item.title,
                    item.url,
                    item.address,
                    item.price_chf,
                    item.area_sqm,
                    item.rooms,
                    int(item.score),
                    item.raw_text,
                    item.image_url or None,
                    item.image_status,
                    item.region,
                    item.currency,
                ),
            )
        self.conn.commit()

    def filter_new(self, listings: list[Listing]) -> list[Listing]:
        new_items: list[Listing] = []
        for item in listings:
            fp = _fingerprint(item)
            exists = self.conn.execute(
                "SELECT 1 FROM seen_listings WHERE fp = ?",
                (fp,),
            ).fetchone()
            if exists:
                continue
            self.conn.execute(
                "INSERT INTO seen_listings (fp, url, title, first_seen_utc) VALUES (?, ?, ?, datetime('now'))",
                (fp, item.url, item.title),
            )
            new_items.append(item)
        self.conn.commit()
        return new_items

    def load_recent_history(self, limit: int = 200) -> list[dict[str, object]]:
        rows = self.conn.execute(
            """
            SELECT
                fetched_at_utc, source_name, category, title, url, address, price_chf, area_sqm, rooms, score
            FROM listings_history
            ORDER BY fetched_at_utc DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        result: list[dict[str, object]] = []
        for row in rows:
            result.append(
                {
                    "fetched_at_utc": row[0],
                    "source_name": row[1],
                    "category": row[2],
                    "title": row[3],
                    "url": row[4],
                    "address": row[5],
                    "price_chf": row[6],
                    "area_sqm": row[7],
                    "rooms": row[8],
                    "score": row[9],
                }
            )
        return result

    def close(self) -> None:
        self.conn.close()

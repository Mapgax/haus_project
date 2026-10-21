from __future__ import annotations

from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .dedupe import _fingerprint
from .models import Listing


class PostgresSeenStore:
    def __init__(self, database_url: str) -> None:
        self.conn = psycopg.connect(database_url, row_factory=dict_row)

    def apply_schema(self, schema_path: str | Path) -> None:
        sql = Path(schema_path).read_text(encoding="utf-8")
        with self.conn.cursor() as cur:
            cur.execute(sql)
        self.conn.commit()

    def record_history(self, listings: list[Listing]) -> None:
        rows = [
            (
                _fingerprint(item),
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
            )
            for item in listings
        ]
        if not rows:
            return
        with self.conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO public.listings_history (
                    fp, source_name, category, title, url, address,
                    price_chf, area_sqm, rooms, score, raw_text,
                    image_url, image_status, region, currency
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (fp) DO UPDATE SET
                    fetched_at_utc = now(),
                    address = EXCLUDED.address,
                    score = EXCLUDED.score,
                    raw_text = EXCLUDED.raw_text,
                    image_url = EXCLUDED.image_url,
                    -- A transient OCR failure ('error') must not flap a sold
                    -- listing back onto the site; a genuine 'clear' verdict
                    -- still overwrites 'sold' (re-evaluated on every scrape).
                    image_status = CASE
                        WHEN EXCLUDED.image_status = 'error'
                             AND listings_history.image_status = 'sold'
                        THEN 'sold'
                        ELSE EXCLUDED.image_status
                    END,
                    region = EXCLUDED.region,
                    currency = EXCLUDED.currency
                """,
                rows,
            )
        self.conn.commit()

    def filter_new(self, listings: list[Listing]) -> list[Listing]:
        new_items: list[Listing] = []
        with self.conn.cursor() as cur:
            for item in listings:
                fp = _fingerprint(item)
                cur.execute(
                    """
                    INSERT INTO public.seen_listings (fp, url, title)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (fp) DO NOTHING
                    RETURNING fp
                    """,
                    (fp, item.url, item.title),
                )
                if cur.fetchone():
                    new_items.append(item)
        self.conn.commit()
        return new_items

    def load_history(self, limit: int | None = None) -> list[dict[str, object]]:
        query = """
            SELECT
                h.fp,
                h.fetched_at_utc,
                h.source_name,
                h.category,
                h.title,
                h.url,
                h.address,
                h.price_chf,
                h.area_sqm,
                h.rooms,
                h.score,
                h.raw_text,
                h.image_url,
                h.image_status,
                h.region,
                h.currency,
                s.first_seen_utc
            FROM public.listings_history h
            LEFT JOIN public.seen_listings s ON s.fp = h.fp
            ORDER BY h.fetched_at_utc DESC
        """
        params: tuple[object, ...] = ()
        if limit is not None:
            query += " LIMIT %s"
            params = (limit,)
        with self.conn.cursor() as cur:
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]

    def close(self) -> None:
        self.conn.close()

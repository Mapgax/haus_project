from __future__ import annotations

import csv
from pathlib import Path
import sqlite3


CONDITION_PLUS = ("modern", "saniert", "renoviert", "neu", "hell", "grosse fenster", "große fenster")
CONDITION_MINUS = ("sanierungsbeduerftig", "sanierungsbedürftig", "renovationsbeduerftig", "renovationsbedürftig", "alt")
LOCATION_HINTS = ("kreuzlingen", "gottlieben", "taegerwilen", "tägerwilen", "bottighofen")


def _normalize(text: str) -> str:
    return (
        (text or "")
        .lower()
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
    )


def _condition_score(text: str) -> int:
    blob = _normalize(text)
    score = 0
    for token in CONDITION_PLUS:
        if _normalize(token) in blob:
            score += 1
    for token in CONDITION_MINUS:
        if _normalize(token) in blob:
            score -= 1
    return score


def _location_bucket(address: str, raw_text: str) -> str:
    blob = _normalize(f"{address} {raw_text}")
    for token in LOCATION_HINTS:
        if _normalize(token) in blob:
            return _normalize(token)
    return "unknown"


def load_training_rows(db_path: str) -> list[dict[str, float | str | int]]:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        """
        SELECT category, source_name, title, address, price_chf, area_sqm, rooms, score, raw_text
        FROM listings_history
        WHERE price_chf IS NOT NULL
          AND area_sqm IS NOT NULL
          AND area_sqm > 0
          AND rooms IS NOT NULL
        """
    ).fetchall()
    conn.close()

    samples: list[dict[str, float | str | int]] = []
    for row in rows:
        category, source_name, title, address, price_chf, area_sqm, rooms, score, raw_text = row
        ppsqm = float(price_chf) / float(area_sqm)
        location = _location_bucket(address or "", raw_text or "")
        condition = _condition_score(f"{title} {raw_text}")
        samples.append(
            {
                "category": str(category),
                "source_name": str(source_name),
                "location_bucket": location,
                "area_sqm": float(area_sqm),
                "rooms": float(rooms),
                "score": int(score),
                "condition_score": int(condition),
                "price_chf": float(price_chf),
                "ppsqm": ppsqm,
            }
        )
    return samples


def export_training_csv(db_path: str, csv_path: str) -> int:
    rows = load_training_rows(db_path)
    out = Path(csv_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "category",
                "source_name",
                "location_bucket",
                "area_sqm",
                "rooms",
                "score",
                "condition_score",
                "price_chf",
                "ppsqm",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


"""Overlay private settings onto the public listings payload.

The committed ``assets/listings.json`` is public and built with neutral defaults
(town-centre reference point, round price limits). The personal values live in
the ``haus_private_settings`` table and are applied here, per request, by the
Vercel function behind the project's login protection.

Everything in this module is pure except ``fetch_settings``; it never raises.
Whatever goes wrong - no settings, bad values, database down - the caller gets
the public payload back unchanged.

The underscore prefix keeps Vercel from deploying this file as a function.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any, Optional

EARTH_RADIUS_KM = 6371.0088  # must match scripts/export_vercel_data.py


def distance_km(ref_lat: float, ref_lon: float, lat: float, lon: float) -> float:
    """Great-circle distance; identical to the exporter's ``_distance_km``."""
    lat1 = math.radians(ref_lat)
    lat2 = math.radians(lat)
    d_lat = lat2 - lat1
    d_lon = math.radians(lon - ref_lon)
    a = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def chf_limit_label(value: float) -> str:
    """Same wording as the exporter, e.g. ``CHF 3 Mio.`` or ``CHF 5'000``."""
    if value >= 1_000_000:
        return f"CHF {value / 1_000_000:g} Mio."
    return "CHF " + f"{value:,.0f}".replace(",", "'")


def _number(value: Any, low: float, high: float) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        return None
    return number


def clean_settings(raw: Any) -> dict[str, Any]:
    """Keep only well-formed values; anything else is silently ignored."""
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, Any] = {}

    ref = raw.get("ref_ch")
    if isinstance(ref, dict):
        label = ref.get("label")
        lat = _number(ref.get("lat"), -90, 90)
        lon = _number(ref.get("lon"), -180, 180)
        if isinstance(label, str) and label.strip() and lat is not None and lon is not None:
            cleaned["ref_ch"] = {"label": label.strip()[:80], "lat": lat, "lon": lon}

    for key in ("buy_limit_chf", "rent_limit_chf"):
        limit = _number(raw.get(key), 1, 1_000_000_000)
        if limit is not None:
            cleaned[key] = limit
    return cleaned


def apply_overlay(payload: dict[str, Any], settings: Any) -> dict[str, Any]:
    """Return a copy of ``payload`` with the private settings applied."""
    out = copy.deepcopy(payload)
    cleaned = clean_settings(settings)
    if not cleaned:
        return out

    ref = cleaned.get("ref_ch")
    if ref:
        references = out.setdefault("references", {})
        references["ch"] = dict(ref)
        for item in out.get("listings", []):
            # The exporter measures every region without its own reference
            # against the Swiss one, so mirror that rule.
            if item.get("region", "ch") in references and item.get("region", "ch") != "ch":
                continue
            lat, lon = item.get("lat"), item.get("lon")
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                item["distanceKm"] = round(distance_km(ref["lat"], ref["lon"], lat, lon), 2)

    for mode, key in (("buy", "buy_limit_chf"), ("rent", "rent_limit_chf")):
        limit = cleaned.get(key)
        criteria = out.get("criteria", {}).get(mode)
        if limit is not None and isinstance(criteria, dict):
            criteria["defaultMaxPriceChf"] = limit
            criteria["priceLimitLabel"] = chf_limit_label(limit)
    return out


def load_static(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def fetch_settings(db_url: Optional[str]) -> dict[str, Any]:
    """Read the settings row; ``{}`` on any problem (never raises)."""
    if not db_url:
        return {}
    try:
        import psycopg

        with psycopg.connect(db_url, connect_timeout=5) as conn:
            row = conn.execute(
                "SELECT settings FROM public.haus_private_settings WHERE id = 1"
            ).fetchone()
        return row[0] if row and isinstance(row[0], dict) else {}
    except Exception as exc:  # noqa: BLE001 - deliberately fail open to public data
        # Log the type only: the message of a connection error can echo the URL.
        print(f"private settings unavailable ({type(exc).__name__})")
        return {}

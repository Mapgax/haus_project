from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from typing import Any
import requests

from hausfinder.config import (
    NL_CONDITION_MINUS,
    NL_CONDITION_PLUS,
    NL_DISCOURAGED_STREETS,
    NL_OBJECT_EXCLUDE,
    NL_OUT_OF_SCOPE,
    NL_PROPERTY_KEYWORDS,
    NL_RECREATION_EXCLUDE,
    NL_WATERFRONT_KEYWORDS,
    NL_WATERFRONT_STREETS,
)
from hausfinder.dashboard import _median, _row_valuation
from hausfinder.filtering import (
    score_listing_nl,
    strip_locality_confounders,
    waterfront_match,
)
from hausfinder.parsing import extract_bouwjaar, is_pagination_url


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _chf_limit_label(value: float) -> str:
    if value >= 1_000_000:
        return f"CHF {value / 1_000_000:g} Mio."
    return "CHF " + f"{value:,.0f}".replace(",", "'")


# The distance reference and the price limits are personal. The committed
# defaults are neutral (town centre / round numbers) so the public repo and
# the daily-committed assets/listings.json never carry them. Override via
# HAUS_REF_CH_* / HAUS_BUY_LIMIT_CHF / HAUS_RENT_LIMIT_CHF only for local or
# private runs - anything set in CI ends up in the committed listings.json.
BUY_LIMIT_CHF = _env_float("HAUS_BUY_LIMIT_CHF", 3_000_000)
RENT_LIMIT_CHF = _env_float("HAUS_RENT_LIMIT_CHF", 5_000)

REFERENCES = {
    "ch": {
        "label": os.environ.get("HAUS_REF_CH_LABEL") or "Kreuzlingen Zentrum",
        "lat": _env_float("HAUS_REF_CH_LAT", 47.6458),
        "lon": _env_float("HAUS_REF_CH_LON", 9.1804),
    },
    "nl": {
        "label": "Oude Rijn, Rijnsburg",
        "lat": 52.19,
        "lon": 4.44,
    },
}

CRITERIA = {
    "buy": {
        "label": "Kaufen",
        "category": "buy",
        "region": "ch",
        "currency": "CHF",
        "minRooms": 5,
        "minAreaSqm": 100,
        "defaultMaxPriceChf": BUY_LIMIT_CHF,
        "priceLimitLabel": _chf_limit_label(BUY_LIMIT_CHF),
    },
    "rent": {
        "label": "Mieten",
        "category": "rent",
        "region": "ch",
        "currency": "CHF",
        "minRooms": 5,
        "minAreaSqm": 100,
        "defaultMaxPriceChf": RENT_LIMIT_CHF,
        "priceLimitLabel": _chf_limit_label(RENT_LIMIT_CHF),
    },
    # No price limit and no area floor by design (user decision); the hard
    # gates are waterfront + no recreation zoning + 4 rooms.
    "nl": {
        "label": "Katwijk (NL)",
        "category": "buy",
        "region": "nl",
        "currency": "EUR",
        "minRooms": 4,
    },
}


def _criteria_key(row: dict[str, Any]) -> str | None:
    """CRITERIA key for a history row: nl rows by region, ch rows by category."""
    if str(row.get("region") or "ch") == "nl":
        return "nl"
    category = str(row.get("category") or "")
    return category if category in ("buy", "rent") else None


def _normalize(text: str) -> str:
    return (
        text.lower()
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
    )


# Coordinates verified against OpenStreetMap/Nominatim geocoding and the
# geo-tagged "Weiherstrasse 13a" RealAdvisor listing. The previous guesses
# were off by 0.5-2.5km (e.g. "weiherstrasse" sat in the town centre), which
# mis-placed AFORIA listings that don't embed their own coordinates.
# "street-guess" is an honest label: this is a street-level approximation,
# not a geocoded house number, so it should not be confused with the
# "listing-geo" precision extracted directly from a listing page.
_RAW_LOCATION_HINTS: list[tuple[str, float, float, str]] = [
    ("Weiherstrasse", 47.6513, 9.1643, "street-guess"),
    ("Neugasse", 47.6435, 9.1685, "street-guess"),
    ("Holzäckerlistrasse", 47.6370, 9.1850, "street-guess"),
    ("Gottlieberstrasse", 47.6598, 9.1361, "street-guess"),
    ("Tägerwilen", 47.6564, 9.1311, "locality"),
    ("Bottighofen", 47.6394, 9.2086, "locality"),
    ("Kreuzlingen", 47.6458, 9.1804, "locality"),
]


def _build_location_hints(
    raw: list[tuple[str, float, float, str]],
) -> dict[str, tuple[float, float, str, str]]:
    """Key hints by their normalized form so an umlaut/ASCII spelling of the
    same street can never be entered twice without immediately failing.
    _coords_for() already normalizes both the hint and the haystack before
    comparing, so two spellings of one street are indistinguishable to it.
    """
    hints: dict[str, tuple[float, float, str, str]] = {}
    for label, lat, lon, precision in raw:
        key = _normalize(label)
        if key in hints:
            raise ValueError(f"Duplicate location hint after normalization: {label!r}")
        hints[key] = (lat, lon, precision, label)
    return hints


LOCATION_HINTS = _build_location_hints(_RAW_LOCATION_HINTS)

# NL fallback table: locality centroids only. Street-level accuracy comes
# from the PDOK Locatieserver (see _geocode_nl); guessing street coordinates
# by hand is exactly what put Kreuzlingen pins in the wrong spot before.
_RAW_NL_LOCATION_HINTS: list[tuple[str, float, float, str]] = [
    ("Rijnsburg", 52.1903, 4.4423, "locality"),
    ("Valkenburg", 52.1806, 4.4300, "locality"),
    ("Katwijk", 52.2010, 4.4100, "locality"),
]

NL_LOCATION_HINTS = _build_location_hints(_RAW_NL_LOCATION_HINTS)

# Rijnsburg centre; the last resort for NL rows — never the Kreuzlingen
# fallback, which would drag Dutch pins onto the Swiss map.
_NL_FALLBACK = (52.1903, 4.4423, "fallback-nl", "Rijnsburg")

PDOK_URL = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"

NL_GEO_CACHE: dict[str, tuple[float, float, str, str] | None] = {}

TARGET_LOCATION_KEYWORDS = (
    "8280",
    "kreuzlingen",
    "taegerwilen",
    "tägerwilen",
    "8598",
    "bottighofen",
)

OUT_OF_SCOPE_LOCATION_KEYWORDS = (
    "8274 gottlieben",
    "gottlieben",
    "deutschland",
    "neuwilen",
    "berg tg",
    "oberhofen",
    "nussbaum",
    "nussbaumen",
    "riedt",
    "erlen",
)


RESIDENTIAL_KEYWORDS = (
    "haus",
    "einfamilienhaus",
    "reihenhaus",
    "villa",
    "landhaus",
    "wohnung",
    "attika",
    "maisonette",
    "duplex",
    "doppelhaus",
)

EXCLUDE_KEYWORDS = (
    "verkauft",
    "vermietet",
    "reserviert",
    "gewerbe",
    "grundstueck",
    "grundstück",
    "bewertung",
    "beraterin",
    "immobilienmakler",
)

# Dutch status/exclusion markers for nl rows (the German list above would
# never fire on Dutch text — and must not gate it either).
NL_EXCLUDE_KEYWORDS = (
    "verkocht",
    "verhuurd",
    "onder bod",
    "ingetrokken",
    "verkauft",  # bilingual broker sites
)

NL_TARGET_LOCALITIES = ("katwijk", "rijnsburg", "valkenburg")

# Kill switch of last resort for sold objects the pipeline cannot detect:
# the only "Verkauft" marker is a banner burned into the title photo, the
# OCR check (imagecheck.py) returns "clear" on it, and the broker still
# serves the object as "active". Currently empty — the one known case
# (Hueppin 1671998, brown text on a cream box) is caught since the OCR
# gained its grayscale --psm 6 pass. Remove an entry once the broker
# actually delists the object.
MANUALLY_DELISTED_URLS: set[str] = set()

GEO_CACHE: dict[str, tuple[bool, tuple[float, float, str, str] | None]] = {}

DEFAULT_EXTRA_URLS = [
    ("buy", "https://realadvisor.ch/de/kaufen/haus/8280-kreuzlingen-YA65-GQ4V"),
]


def _clean_price(value: Any, category: str) -> float | None:
    if value is None:
        return None
    price = float(value)
    if category == "buy" and price < 100_000:
        return None
    if category == "rent" and (price < 500 or price > 50_000):
        return None
    return price


def _clean_address(value: str) -> str:
    # A contact block ("kauf@broker.ch … +41 71 …" / "+31 71 …") is the
    # broker's own footer address, not the property's — it must neither
    # satisfy the location filter nor place a pin on the map.
    if "@" in value or re.search(r"\+\d{2}\s?\d", value):
        return ""
    address = re.sub(r"\bCHF\s+[0-9'’.,]+", "", value).strip()
    address = re.sub(r"\b[0-9][0-9'’.,]*\s*CHF\b", "", address).strip()
    address = re.sub(r"^\s*[-–]\s*", "", address)
    return re.sub(r"\s{2,}", " ", address)


def _property_type(text: str) -> str:
    norm = _normalize(text)
    title_part = _normalize(text[:180])
    # Dutch "huis"/"landhuis" are covered by the German "haus" substring.
    house_words = ("haus", "villa", "landhaus", "woning", "bungalow", "woonboerderij")
    flat_words = ("wohnung", "attika", "maisonette", "duplex", "appartement")
    if any(word in title_part for word in house_words):
        return "Haus"
    if any(word in title_part for word in flat_words):
        return "Wohnung"
    if any(word in norm for word in house_words):
        return "Haus"
    if any(word in norm for word in flat_words):
        return "Wohnung"
    return "Objekt"


def _coords_for(address: str, fallback_text: str) -> tuple[float, float, str, str]:
    # Match the address field first. Falling back to the full scraped text
    # (title + raw description) risks pinning a listing to a street that's
    # merely mentioned as a nearby landmark rather than where it actually is.
    for haystack in (address, fallback_text):
        norm = _normalize(haystack)
        for key, (lat, lon, precision, label) in LOCATION_HINTS.items():
            if key in norm:
                return lat, lon, precision, label
    return 47.6458, 9.1804, "fallback", "Kreuzlingen"


def _pdok_lookup(query: str) -> tuple[float, float, str, str] | None:
    """Geocode via the free Dutch national Locatieserver (no API key).

    Prefers exact addresses over street ('weg') results so a house number
    resolves to the house, not the street middle — town-centre pins are the
    documented failure mode this project must avoid.
    """
    if query in NL_GEO_CACHE:
        return NL_GEO_CACHE[query]
    result: tuple[float, float, str, str] | None = None
    try:
        response = requests.get(
            PDOK_URL,
            params={"q": query, "rows": 5, "fl": "centroide_ll,weergavenaam,type"},
            headers={"User-Agent": "HausfinderBot/0.1 (+personal property search)"},
            timeout=12,
        )
        response.raise_for_status()
        docs = response.json().get("response", {}).get("docs", [])
        for wanted in ("adres", "weg", "woonplaats"):
            for doc in docs:
                if doc.get("type") != wanted:
                    continue
                match = re.match(
                    r"POINT\(([0-9.\-]+) ([0-9.\-]+)\)", str(doc.get("centroide_ll") or "")
                )
                if not match:
                    continue
                lon, lat = float(match.group(1)), float(match.group(2))
                result = (lat, lon, f"pdok-{wanted}", str(doc.get("weergavenaam") or query))
                break
            if result:
                break
    except (requests.RequestException, ValueError):
        result = None
    NL_GEO_CACHE[query] = result
    return result


def _coords_for_nl(address: str, fallback_text: str) -> tuple[float, float, str, str]:
    if address:
        pdok = _pdok_lookup(address)
        if pdok:
            return pdok
    # A matched whitelist street without a usable address still beats the
    # locality centroid: geocode the street within the matched town.
    street = waterfront_match(fallback_text, NL_WATERFRONT_STREETS, ())
    if street:
        for locality in ("Katwijk", "Rijnsburg", "Valkenburg ZH"):
            if _normalize(locality.split()[0]) in _normalize(fallback_text):
                pdok = _pdok_lookup(f"{street}, {locality}")
                if pdok:
                    return pdok
                break
    for haystack in (address, fallback_text):
        norm = _normalize(haystack)
        for key, (lat, lon, precision, label) in NL_LOCATION_HINTS.items():
            if key in norm:
                return lat, lon, precision, label
    return _NL_FALLBACK


_GEO_PATTERNS = [
    r'"latitude"\s*:\s*([0-9.]+)\s*,\s*"longitude"\s*:\s*([0-9.]+)',
    r'"lat"\s*:\s*([0-9.]+)\s*,\s*"lng"\s*:\s*([0-9.]+)',
]

# Plausibility windows per region: Thurgau/Bodensee vs. Dutch Randstad.
_GEO_BOUNDS = {
    "ch": (47.0, 48.0, 8.0, 10.0),
    "nl": (51.5, 53.5, 3.3, 7.3),
}


def _extract_geo(text: str, region: str = "ch") -> tuple[float, float, str, str] | None:
    lat_min, lat_max, lon_min, lon_max = _GEO_BOUNDS.get(region, _GEO_BOUNDS["ch"])
    for pattern in _GEO_PATTERNS:
        match = re.search(pattern, text)
        if not match:
            continue
        lat = float(match.group(1))
        lon = float(match.group(2))
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return (lat, lon, "listing-geo", "Inseratskoordinaten")
    return None


def _listing_status_from_url(
    url: str, region: str = "ch"
) -> tuple[bool, tuple[float, float, str, str] | None]:
    """Check whether a listing detail page is still live and extract geo hints.

    A hard 404/410 means the listing is gone on any source. RealAdvisor
    additionally does not 404 delisted (sold/removed) listings: it
    308-redirects the old detail URL to the generic category search page
    (e.g. /de/kaufen/8280-kreuzlingen/haus). requests follows that redirect
    transparently, so a non-empty response.history is the signal that the
    specific listing is gone. Everything else (403 bot walls, timeouts,
    5xx) fails open so a transient error cannot hide a listing.
    """
    if not url.startswith("http"):
        return True, None
    if url in GEO_CACHE:
        return GEO_CACHE[url]
    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": "HausfinderBot/0.1 (+personal property search)",
                "Accept-Language": "de-CH,de;q=0.9,en;q=0.8",
            },
            timeout=12,
        )
    except requests.RequestException:
        GEO_CACHE[url] = (True, None)
        return True, None

    if response.status_code in (404, 410):
        GEO_CACHE[url] = (False, None)
        return False, None
    if not response.ok:
        GEO_CACHE[url] = (True, None)
        return True, None
    if "realadvisor.ch" in url and response.history:
        GEO_CACHE[url] = (False, None)
        return False, None

    result = (True, _extract_geo(response.text, region))
    GEO_CACHE[url] = result
    return result


def _distance_km(lat: float, lon: float, region: str = "ch") -> float:
    reference = REFERENCES.get(region, REFERENCES["ch"])
    radius_km = 6371.0088
    lat1 = math.radians(reference["lat"])
    lat2 = math.radians(lat)
    d_lat = lat2 - lat1
    d_lon = math.radians(lon - reference["lon"])
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    )
    return 2 * radius_km * math.asin(math.sqrt(a))


def _quality_flag(row: dict[str, Any], text: str) -> str | None:
    category = str(row.get("category") or "")
    price = _clean_price(row.get("price_chf"), category)
    rooms = row.get("rooms")
    area = row.get("area_sqm")
    if price is None:
        return "Preis auf Anfrage oder nicht sauber extrahiert"
    if rooms is None or area is None:
        return "Zimmer/Flaeche nicht vollstaendig extrahiert"
    if "auf anfrage" in _normalize(text):
        return "Preis auf Anfrage im Quelltext"
    return None


def _in_target_location(text: str) -> bool:
    norm = _normalize(text)
    if any(_normalize(keyword) in norm for keyword in OUT_OF_SCOPE_LOCATION_KEYWORDS):
        return False
    return any(_normalize(keyword) in norm for keyword in TARGET_LOCATION_KEYWORDS)


def _is_candidate_nl(row: dict[str, Any]) -> bool:
    """Export gate for nl rows: re-applies the hard NL filters against the
    stored row, so tightening a keyword list takes effect on the next export
    without waiting for a re-scrape."""
    title = str(row.get("title") or "")
    address = _clean_address(str(row.get("address") or ""))
    raw_text = str(row.get("raw_text") or "")
    url = str(row.get("url") or "")
    text = f"{title} {address} {raw_text} {url}"
    norm = _normalize(text)
    status_text = _normalize(f"{title} {address} {url}")

    if any(_normalize(kw) in status_text for kw in NL_EXCLUDE_KEYWORDS):
        return False
    # Bare berths/garages/plots ("Ligplaats 50") — never the house.
    if any(_normalize(kw) in _normalize(title) for kw in NL_OBJECT_EXCLUDE):
        return False
    if not any(_normalize(kw) in norm for kw in NL_PROPERTY_KEYWORDS):
        return False
    # Street names containing a town name (rijnsburgerweg in Oegstgeest)
    # must not satisfy or trip the locality gates.
    norm_localities = strip_locality_confounders(norm)
    if any(_normalize(kw) in norm_localities for kw in NL_OUT_OF_SCOPE):
        return False
    if not any(_normalize(loc) in norm_localities for loc in NL_TARGET_LOCALITIES):
        return False
    if any(_normalize(street) in norm for street in NL_DISCOURAGED_STREETS):
        return False
    if any(_normalize(kw) in norm for kw in NL_RECREATION_EXCLUDE):
        return False
    if waterfront_match(text, NL_WATERFRONT_STREETS, NL_WATERFRONT_KEYWORDS) is None:
        return False
    rooms = row.get("rooms")
    if rooms is not None and (float(rooms) < 4 or float(rooms) > 12):
        return False
    # No price cap and no area floor by design (user decision).
    return True


def _is_candidate(row: dict[str, Any]) -> bool:
    # OCR verdict from the scrape run: a sold banner in the title image.
    # Excluded only here at export time — the row keeps its history and is
    # re-evaluated on every scrape, so a false positive heals itself.
    if str(row.get("image_status") or "") == "sold":
        return False
    # Overview pagination pages that older scraper versions crawled as fake
    # "detail pages" (Hueppin "IMMOBILIEN KAUFEN", /immobilien/page/2/…).
    # Their rows live in the history forever, so the export must reject the
    # URL shape too — the scraper-side skip alone only prevents new rows.
    row_url = str(row.get("url") or "")
    if is_pagination_url(row_url):
        return False
    if row_url in MANUALLY_DELISTED_URLS:
        return False
    if _criteria_key(row) == "nl":
        return _is_candidate_nl(row)
    category = str(row.get("category") or "")
    if category not in ("buy", "rent"):
        return False
    title = str(row.get("title") or "")
    address = _clean_address(str(row.get("address") or ""))
    raw_text = str(row.get("raw_text") or "")
    url = str(row.get("url") or "")
    text = f"{title} {address} {raw_text} {url}"
    norm = _normalize(text)
    status_text = _normalize(f"{title} {address} {url}")

    if category == "buy" and ("mieten" in status_text or "/mieten/" in url):
        return False
    if category == "rent" and (
        "/kaufen/" in url
        or "-kaufen-" in url
        or url.endswith("-kaufen/")
        or " kaufen" in status_text
        or " zum kauf" in norm
    ):
        return False
    if category == "rent" and not any(
        hint in norm
        for hint in ("mieten", "miete", "mietzins", "monatlich", "pro monat", "/mt")
    ):
        return False
    if any(keyword in status_text for keyword in EXCLUDE_KEYWORDS):
        return False
    if not any(keyword in norm for keyword in RESIDENTIAL_KEYWORDS):
        return False
    if title.strip().lower() in {"immobilien", "immobilienangebot", "immobilien\u00adangebot"}:
        return False
    if re.search(r"/immobilien/?(?:\?|$)", url):
        return False
    if not _in_target_location(status_text):
        return False

    rooms = row.get("rooms")
    area = row.get("area_sqm")
    if rooms is None or area is None:
        return False
    rooms = float(rooms)
    area = float(area)
    criteria = CRITERIA[category]
    if rooms < float(criteria["minRooms"]) or rooms > 12:
        return False
    if area < float(criteria["minAreaSqm"]) or area > 900:
        return False
    return True


def _iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _price_label(price: float | None, currency: str) -> str:
    if not price:
        return "Auf Anfrage"
    if currency == "EUR":
        return f"€ {price:,.0f}".replace(",", ".")
    return f"CHF {price:,.0f}".replace(",", "'")


def _row_to_listing(row: dict[str, Any]) -> dict[str, Any]:
    title = str(row.get("title") or "Objekt ohne Titel").strip()
    address = _clean_address(str(row.get("address") or "").strip())
    raw_text = str(row.get("raw_text") or "")
    url = str(row.get("url") or "")
    category = str(row.get("category") or "")
    region = "nl" if str(row.get("region") or "ch") == "nl" else "ch"
    currency = "EUR" if region == "nl" else str(row.get("currency") or "CHF")
    text = f"{title} {address} {raw_text}"
    is_alive, geo = _listing_status_from_url(url, region)
    if geo is None:
        # ImmoStreet result cards embed their exact coordinates in raw_text.
        geo = _extract_geo(raw_text, region)
    if geo:
        lat, lon, precision, location_label = geo
    elif region == "nl":
        lat, lon, precision, location_label = _coords_for_nl(address, text)
    else:
        lat, lon, precision, location_label = _coords_for(address, text)
    price = _clean_price(row.get("price_chf"), category)
    distance = _distance_km(lat, lon, region)
    # NL rows may miss area (no floor by design); the CH gate guarantees both.
    area = float(row["area_sqm"]) if row.get("area_sqm") is not None else None
    rooms = float(row["rooms"]) if row.get("rooms") is not None else None
    first_seen = _iso_or_none(row.get("first_seen_utc")) or _iso_or_none(
        row.get("fetched_at_utc")
    )
    listing = {
        "id": str(row.get("fp") or row.get("url")),
        "isAlive": is_alive,
        "category": category,
        "region": region,
        "currency": currency,
        "source": row.get("source_name"),
        "title": title,
        "propertyType": _property_type(text),
        "address": address or location_label,
        "locationLabel": location_label,
        "priceChf": price,
        "priceLabel": _price_label(price, currency),
        "areaSqm": area,
        "rooms": rooms,
        "url": row.get("url"),
        "lat": lat,
        "lon": lon,
        "distanceKm": round(distance, 2),
        "geoPrecision": precision,
        "qualityNote": _quality_flag(row, text),
        "pricePerSqm": round(price / area) if price and area else None,
        "firstSeen": first_seen,
    }
    if region == "nl":
        norm = _normalize(f"{text} {url}")
        listing["waterfront"] = waterfront_match(
            f"{text} {url}", NL_WATERFRONT_STREETS, NL_WATERFRONT_KEYWORDS
        )
        listing["preferredStreet"] = any(
            _normalize(street) in _normalize(f"{title} {address}")
            for street in NL_WATERFRONT_STREETS
        )
        listing["bouwjaar"] = extract_bouwjaar(raw_text)
        listing["conditionPlus"] = [
            kw for kw in NL_CONDITION_PLUS if _normalize(kw) in norm
        ]
        listing["conditionMinus"] = [
            kw for kw in NL_CONDITION_MINUS if _normalize(kw) in norm
        ]
        # Recomputed at export (not read from the stored score) so keyword
        # tweaks re-rank existing rows without a re-scrape.
        listing["nlScore"] = _score_nl_row(title, address, raw_text)
    return listing


def _score_nl_row(title: str, address: str, raw_text: str) -> int:
    from hausfinder.config import NlSearchFilters

    return score_listing_nl(title, address, raw_text, NlSearchFilters())


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    seen_urls: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda x: (x["distanceKm"], x["priceChf"] or 10**12)):
        # The same listing can be discovered twice (e.g. via a source's
        # overview page and via a forced extra URL) with slightly different
        # parsed area/rooms each time, which would otherwise defeat the
        # fuzzy key below. An identical URL is always the same listing.
        url_key = (item["url"] or "").strip().lower()
        if url_key and url_key in seen_urls:
            continue
        if url_key:
            seen_urls.add(url_key)
        # NL rows may carry no parsed area (no floor by design).
        area_key = (
            str(round(item["areaSqm"])) if item["areaSqm"] is not None else "na"
        )
        keys = [
            "|".join(
                [
                    _normalize(item["title"])[:60],
                    item["category"],
                    area_key,
                    str(item["rooms"]),
                    item["locationLabel"],
                ]
            ),
            # The same object listed on several portals carries different
            # titles and URLs, but category + rooms + area + price is a
            # near-unique signature. Without a price, fall back to the
            # location label to avoid merging distinct "Auf Anfrage"
            # objects in different places.
            "|".join(
                [
                    item["category"],
                    str(item["rooms"]),
                    area_key,
                    str(int(item["priceChf"])) if item["priceChf"] else item["locationLabel"],
                ]
            ),
        ]
        if any(key in seen for key in keys):
            continue
        seen.update(keys)
        result.append(item)
    return result


def _load_sqlite(db_path: Path) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(listings_history)")
        }
        # Older local databases predate these columns; select them only when
        # present so the export keeps working on stale files.
        extra = [
            name
            for name in ("image_url", "image_status", "region", "currency")
            if name in columns
        ]
        extra_sql = ("," + ", ".join(f"h.{name}" for name in extra)) if extra else ""
        rows = conn.execute(
            f"""
            SELECT
                h.fp, h.fetched_at_utc, h.source_name, h.category, h.title,
                h.url, h.address, h.price_chf, h.area_sqm, h.rooms, h.score,
                h.raw_text, s.first_seen_utc{extra_sql}
            FROM listings_history h
            LEFT JOIN seen_listings s ON s.fp = h.fp
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _load_extra_urls(urls: list[tuple[str, str]]) -> list[dict[str, Any]]:
    from dataclasses import asdict
    from hausfinder.sources import _extract_listing_from_detail_page

    rows: list[dict[str, Any]] = []
    for category, url in urls:
        try:
            response = requests.get(
                url,
                headers={
                    "User-Agent": "HausfinderBot/0.1 (+personal property search)",
                    "Accept-Language": "de-CH,de;q=0.9,en;q=0.8",
                },
                timeout=12,
            )
            response.raise_for_status()
        except requests.RequestException:
            continue
        source_name = "RealAdvisor" if "realadvisor.ch" in url else "Manual"
        item = _extract_listing_from_detail_page(url, response.text, source_name, category)
        rows.append(_listing_to_row(item))
    return rows


def _listing_to_row(item: Any) -> dict[str, Any]:
    row = asdict(item)
    row["source_name"] = row.pop("source_name")
    row["price_chf"] = row.pop("price_chf")
    row["area_sqm"] = row.pop("area_sqm")
    row["raw_text"] = row.pop("raw_text")
    return row


def _load_live(
    config_path: str,
    source_names: set[str] | None,
    extra_urls: list[str],
) -> list[dict[str, Any]]:
    from hausfinder.config import load_config
    from hausfinder.filtering import apply_filters
    from hausfinder.sources import fetch_all_sources

    cfg = load_config(config_path)
    if source_names:
        cfg.sources = [source for source in cfg.sources if source.name in source_names]
    cfg.request_timeout_sec = min(cfg.request_timeout_sec, 12)
    for source in cfg.sources:
        source.max_links_per_source = min(source.max_links_per_source, 18)
    raw = fetch_all_sources(cfg)
    filtered = apply_filters(raw, cfg.filters, cfg.filters_nl)
    rows: list[dict[str, Any]] = []
    for item in filtered:
        rows.append(_listing_to_row(item))
    rows.extend(_load_extra_urls(extra_urls))
    return rows


def _latest_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only the newest history row per URL.

    The history table keeps every parsed snapshot of a listing forever, so a
    re-scrape with improved parsing (or a corrected buy/rent category) leaves
    the old, wrong snapshot in place next to the new one. One URL is one
    object — only the freshest snapshot should reach the site.
    """
    ordered = sorted(
        rows, key=lambda r: str(r.get("fetched_at_utc") or ""), reverse=True
    )
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in ordered:
        key = str(row.get("url") or "").strip().lower()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        result.append(row)
    return result


# Rows the current scrape run did not refresh are delisted at their source
# and must not reach the site. The daily run refreshes every live listing's
# fetched_at right before the export, so live rows are minutes old and rows
# missed by the run are ~24h old; 20h reliably separates the two even when
# run times drift. This is what retires listings on sources whose delisted
# pages cannot be probed directly (ImmoScout24 answers 403 to non-browsers,
# so _listing_status_from_url fails open for them forever). Deliberate
# tradeoff: a transient per-source fetch failure hides that source's
# listings for one day; they return with the next successful run. Only the
# scrape pipeline opts in (export_data max_row_age_hours) — a standalone
# `--source sqlite` export reads a static file nothing refreshes and would
# otherwise always come out empty.
MAX_ROW_AGE_HOURS = 20


def _row_age_hours(row: dict[str, Any], now: datetime) -> float | None:
    """Age of a history row, or None when it carries no usable timestamp
    (extra-URL rows are fetched live during the export and have none)."""
    value = row.get("fetched_at_utc")
    if value is None:
        return None
    if isinstance(value, datetime):
        stamp = value
    else:
        try:
            stamp = datetime.fromisoformat(str(value).strip())
        except ValueError:
            return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (now - stamp).total_seconds() / 3600


def _fresh_rows(
    rows: list[dict[str, Any]],
    max_age_hours: float,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    return [
        row
        for row in rows
        if (age := _row_age_hours(row, now)) is None or age <= max_age_hours
    ]


# Below this many priced listings a per-category median is too noisy to
# grade against (rent currently has 2 objects - it gets no verdicts).
MIN_VALUATION_SAMPLES = 5

# dashboard._row_valuation verdicts -> compact JSON keys; "unklar" -> None.
_VERDICT_KEYS = {"eher guenstig": "guenstig", "mittel": "mittel", "eher teuer": "teuer"}


def _listing_group(item: dict[str, Any]) -> str:
    """Stats/sort group: nl rows by region, ch rows by category — EUR prices
    must never move the CHF medians (and vice versa)."""
    return "nl" if item.get("region") == "nl" else item["category"]


def _compute_price_stats(
    listings: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    """Attach a median-based `priceValuation` to every listing in place and
    return per-group stats for the payload.

    The median is computed strictly per group so the handful of rent
    objects can never skew the buy median (and vice versa).
    """
    stats: dict[str, dict[str, int]] = {}
    for group in ("buy", "rent", "nl"):
        group_items = [item for item in listings if _listing_group(item) == group]
        values = [
            float(item["pricePerSqm"]) for item in group_items if item.get("pricePerSqm")
        ]
        median = _median(values)
        eligible = median is not None and len(values) >= MIN_VALUATION_SAMPLES
        if eligible:
            stats[group] = {"medianPpsqm": round(median), "count": len(values)}
        for item in group_items:
            # NL has no price limit by decision — no cheap/expensive verdicts.
            item["priceValuation"] = (
                _VERDICT_KEYS.get(_row_valuation(item.get("pricePerSqm"), median))
                if eligible and group != "nl"
                else None
            )
    return stats


def export_data(
    rows: list[dict[str, Any]],
    output_path: Path,
    source: str,
    max_row_age_hours: float | None = None,
) -> None:
    # Freshness before _latest_rows: a stale history row must not first win
    # the per-URL dedupe against its freshly fetched extra-URL twin (which
    # carries no timestamp) and then get dropped — that would remove exactly
    # the manually pinned listings the extra-URL mechanism exists to keep.
    if max_row_age_hours is not None:
        rows = _fresh_rows(rows, max_row_age_hours)
    rows = _latest_rows(rows)
    listings = [_row_to_listing(row) for row in rows if _is_candidate(row)]
    listings = [item for item in listings if item.pop("isAlive")]
    listings = _dedupe(listings)
    # CH tabs sort by distance to the reference; the NL tab sorts by the
    # surroundings/condition score — distance to the Oude Rijn reference
    # point carries no meaning for a search that is waterfront-gated anyway.
    listings.sort(
        key=lambda x: (
            _listing_group(x),
            -x.get("nlScore", 0) if _listing_group(x) == "nl" else x["distanceKm"],
            x["priceChf"] or 10**12,
        )
    )
    price_stats = _compute_price_stats(listings)
    stable_payload = {
        "source": source,
        "references": REFERENCES,
        "criteria": CRITERIA,
        "priceStats": price_stats,
        "listings": listings,
    }
    if output_path.exists():
        existing = json.loads(output_path.read_text(encoding="utf-8"))
        existing_stable = {k: existing.get(k) for k in stable_payload}
        if existing_stable == stable_payload:
            print(f"No listing changes for {output_path}")
            return

    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        **stable_payload,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Exported {len(listings)} listings to {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Export static data for the Vercel UI.")
    parser.add_argument("--db-path", default="data/seen.sqlite3")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--output", default="assets/listings.json")
    parser.add_argument("--source", choices=["sqlite", "live"], default="sqlite")
    parser.add_argument(
        "--live-sources",
        default=(
            "AFORIA Immobilien,Goldinger,Immokanzlei,Lebenswerk (neu),"
            "Raumteam,RealAdvisor,Retronova Immobilien,Sutter Immobilien"
        ),
        help="Comma-separated source names for live export. Empty string scans all sources.",
    )
    parser.add_argument(
        "--extra-url",
        action="append",
        default=[],
        help="Additional known buy listing detail URL to include even if it is not linked from an overview page.",
    )
    args = parser.parse_args()

    if args.source == "live":
        source_names = {name.strip() for name in args.live_sources.split(",") if name.strip()}
        extra_urls = [
            *DEFAULT_EXTRA_URLS,
            *(("buy", url) for url in args.extra_url),
        ]
        rows = _load_live(args.config, source_names or None, extra_urls)
    else:
        rows = _load_sqlite(Path(args.db_path))
    export_data(rows, Path(args.output), args.source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

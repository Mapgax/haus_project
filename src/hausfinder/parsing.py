from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse


# Swiss listings write prices both ways ("CHF 850'000" and "850'000 CHF") and
# use straight or typographic apostrophes as thousands separators.
_NUM = r"[0-9][0-9'’‘  .,]*"
PRICE_BEFORE_RE = re.compile(rf"(?:CHF|Fr\.?)\s*({_NUM})", re.IGNORECASE)
PRICE_AFTER_RE = re.compile(rf"({_NUM})\s*(?:CHF|Fr\.)", re.IGNORECASE)

# "5.5 Zimmer", "5.5-Zimmer-Wohnung", "5½ Zimmer", "5 1/2 Zimmer", "4,5 Zi."
ROOMS_HALF_RE = re.compile(r"([0-9]+)\s*(?:½|1\s*/\s*2)\s*[-\s]*Zi(?:mmer)?\b", re.IGNORECASE)
ROOMS_RE = re.compile(r"([0-9]+(?:[.,][0-9]+)?)\s*[-\s]*Zi(?:mmer)?\b", re.IGNORECASE)

# BeautifulSoup's get_text(separator=" ") turns "m<sup>2</sup>" into "m 2",
# so the unit must tolerate whitespace before the exponent.
_AREA_UNIT = r"(?:m\s*[²2]|qm)"
AREA_LABELED_RE = re.compile(
    rf"Wohnfl(?:ä|ae)che\D{{0,20}}?([0-9]+(?:[.,][0-9]+)?)\s*{_AREA_UNIT}",
    re.IGNORECASE,
)
AREA_RE = re.compile(rf"([0-9]+(?:[.,][0-9]+)?)\s*{_AREA_UNIT}\b", re.IGNORECASE)


def parse_swiss_number(value: str) -> float | None:
    cleaned = (
        value.replace("'", "")
        .replace("’", "")
        .replace("‘", "")
        .replace(" ", "")
        .replace(" ", "")
        .replace(" ", "")
        .strip(".,-–")
    )
    cleaned = cleaned.replace(",", ".")
    # More than one dot means German-style thousands grouping ("2.450.000").
    if cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


# Three or more currency amounts in direct succession are a search-widget
# dropdown ("Min Preis CHF 1'000 CHF 5'000 CHF 10'000 …"), not a listing
# price — some broker sites (e.g. Retronova) render one on every detail page.
_PRICE_DROPDOWN_RE = re.compile(
    rf"(?:(?:CHF|Fr\.?)\s*{_NUM}\s*){{3,}}", re.IGNORECASE
)


def extract_price_chf(text: str) -> float | None:
    text = _PRICE_DROPDOWN_RE.sub(" ", text)
    # Take the larger of the two first matches: in card text like
    # "850'000 CHF 3.5 Zimmer" the CHF-first pattern would otherwise
    # grab "CHF 3.5" and report the room count as the price.
    candidates = []
    for pattern in (PRICE_BEFORE_RE, PRICE_AFTER_RE):
        match = pattern.search(text)
        if match:
            value = parse_swiss_number(match.group(1))
            if value:
                candidates.append(value)
    return max(candidates) if candidates else None


def extract_rooms(text: str) -> float | None:
    match = ROOMS_HALF_RE.search(text)
    if match:
        whole = parse_swiss_number(match.group(1))
        return whole + 0.5 if whole is not None else None
    match = ROOMS_RE.search(text)
    if not match:
        return None
    return parse_swiss_number(match.group(1))


def extract_area_sqm(text: str) -> float | None:
    match = AREA_LABELED_RE.search(text) or AREA_RE.search(text)
    if not match:
        return None
    return parse_swiss_number(match.group(1))


# --- Dutch (NL) parsing -----------------------------------------------------
# Dutch prices group thousands with dots ("€ 750.000 k.k."), the exact
# opposite of parse_swiss_number, which would read "750.000" as 750.0.

_NUM_NL = r"[0-9][0-9. ,]*"
PRICE_EUR_BEFORE_RE = re.compile(rf"(?:€|EUR)\s*({_NUM_NL})", re.IGNORECASE)
PRICE_EUR_AFTER_RE = re.compile(rf"({_NUM_NL})\s*(?:€|EUR)", re.IGNORECASE)

# Search-widget dropdowns render runs of € amounts, same as the CHF case.
_PRICE_EUR_DROPDOWN_RE = re.compile(rf"(?:(?:€|EUR)\s*{_NUM_NL}\s*){{3,}}", re.IGNORECASE)

# "5 kamers" / "5-kamerwoning" counts all rooms; "4 slaapkamers" counts
# bedrooms only, so the living room is added below. The kamer pattern cannot
# fire inside "slaapkamers" because the digit is separated from "kamer" by
# the word "slaap", which neither \s nor - matches.
ROOMS_NL_SLAAP_RE = re.compile(r"([0-9]+)\s*slaapkamers?\b", re.IGNORECASE)
ROOMS_NL_RE = re.compile(
    r"([0-9]+)[-\s]*kamer(?:s|woning|appartement)?\b", re.IGNORECASE
)

# Prefer the labeled living area so the perceel (plot) size never wins.
AREA_NL_LABELED_RE = re.compile(
    rf"woonoppervlak(?:te)?\D{{0,20}}?([0-9]+(?:[.,][0-9]+)?)\s*{_AREA_UNIT}",
    re.IGNORECASE,
)

BOUWJAAR_RE = re.compile(r"bouwjaar\s*:?\s*((?:18|19|20)[0-9]{2})\b", re.IGNORECASE)


def parse_dutch_number(value: str) -> float | None:
    cleaned = (
        value.replace(" ", "")
        .replace(" ", "")
        .replace(" ", "")
        .strip(".,-–")
    )
    if not cleaned:
        return None
    # Dots are thousands separators, a comma is the decimal mark.
    cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_price_eur(text: str) -> float | None:
    text = _PRICE_EUR_DROPDOWN_RE.sub(" ", text)
    candidates = []
    for pattern in (PRICE_EUR_BEFORE_RE, PRICE_EUR_AFTER_RE):
        match = pattern.search(text)
        if match:
            value = parse_dutch_number(match.group(1))
            if value:
                candidates.append(value)
    return max(candidates) if candidates else None


def extract_rooms_nl(text: str) -> float | None:
    match = ROOMS_NL_RE.search(text)
    if match:
        return parse_dutch_number(match.group(1))
    match = ROOMS_NL_SLAAP_RE.search(text)
    if match:
        bedrooms = parse_dutch_number(match.group(1))
        # Dutch convention: kamers ≈ slaapkamers + living room.
        return bedrooms + 1 if bedrooms is not None else None
    return None


def extract_area_sqm_nl(text: str) -> float | None:
    match = AREA_NL_LABELED_RE.search(text) or AREA_RE.search(text)
    if not match:
        return None
    return parse_dutch_number(match.group(1))


def extract_bouwjaar(text: str) -> int | None:
    match = BOUWJAAR_RE.search(text)
    if not match:
        return None
    year = int(match.group(1))
    return year if 1850 <= year <= 2030 else None


def absolute_url(base_url: str, candidate: str) -> str:
    return urljoin(base_url, candidate)


def _canonical_host(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def same_host(url_a: str, url_b: str) -> bool:
    return _canonical_host(url_a) == _canonical_host(url_b)


# Overview pagination URLs ("/immobilien/page/2/…", "?pageNum=3") match the
# listing-link keywords of several brokers and once became a fake map pin
# carrying a sold house's address. Shared here because both the scraper
# (skip while collecting links) and the export (reject rows already in the
# history) must agree on the shape.
_PAGINATION_URL_RE = re.compile(r"/page/\d+|[?&]page(?:num)?=\d+", re.IGNORECASE)


def is_pagination_url(url: str) -> bool:
    return bool(_PAGINATION_URL_RE.search(url))

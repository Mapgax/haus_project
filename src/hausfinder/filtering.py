from __future__ import annotations

from .config import (
    NL_LOCALITY_CONFOUNDERS,
    NL_PROPERTY_KEYWORDS,
    NlSearchFilters,
    SearchFilters,
)
from .models import Listing
from .parsing import extract_bouwjaar


PROPERTY_KEYWORDS = (
    "haus",
    "einfamilienhaus",
    "reihenhaus",
    "doppelhaus",
    "villa",
    "wohnung",
    "attika",
    "maisonette",
    "erdgeschoss",
    "garten",
)
QUALITY_PLUS = ("garten", "gross", "groß", "fenster", "modern", "renovierbar", "saniert")


def _normalize(text: str) -> str:
    return (
        text.lower()
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
    )


def _text_blob(listing: Listing) -> str:
    return _normalize(f"{listing.title} {listing.address} {listing.raw_text}")


def _location_match(listing: Listing, filters: SearchFilters) -> bool:
    strict_text = _normalize(f"{listing.title} {listing.address}")
    locality_match = any(_normalize(x) in strict_text for x in filters.target_localities)
    postcode_match = any(p in strict_text for p in filters.target_postcodes)
    if locality_match or postcode_match:
        return True
    if filters.strict_location_match:
        return False
    if not filters.location_fallback_to_raw_text:
        return False
    relaxed_text = _normalize(listing.raw_text)
    locality_match = any(_normalize(x) in relaxed_text for x in filters.target_localities)
    postcode_match = any(p in relaxed_text for p in filters.target_postcodes)
    return locality_match or postcode_match


def _neighborhood_constraints_ok(text: str, filters: SearchFilters) -> bool:
    preferred_hit = any(_normalize(x) in text for x in filters.preferred_neighborhoods)
    discouraged_hit = any(
        _normalize(x) in text for x in filters.discouraged_neighborhoods
    )
    if discouraged_hit:
        return False
    if preferred_hit:
        return True
    return filters.allow_ambiguous_neighborhood


def _price_ok(listing: Listing, filters: SearchFilters) -> bool:
    if listing.price_chf is None:
        return True
    if listing.category == "buy":
        return listing.price_chf <= filters.buy_price_limit_chf
    return listing.price_chf <= filters.rent_price_limit_chf


def _rooms_ok(listing: Listing, filters: SearchFilters) -> bool:
    return listing.rooms is None or listing.rooms >= filters.min_rooms


def _area_ok(listing: Listing, filters: SearchFilters) -> bool:
    return listing.area_sqm is None or listing.area_sqm >= filters.min_area_sqm


def _type_hint_ok(text: str) -> bool:
    return any(keyword in text for keyword in PROPERTY_KEYWORDS)


def score_listing(listing: Listing, filters: SearchFilters) -> int:
    text = _text_blob(listing)
    score = 0
    for p in filters.preferred_neighborhoods:
        if _normalize(p) in text:
            score += 3
    for n in filters.discouraged_neighborhoods:
        if _normalize(n) in text:
            score -= 2
    for q in QUALITY_PLUS:
        if q in text:
            score += 1
    return score


# --- Netherlands (region "nl") ----------------------------------------------


def strip_locality_confounders(normalized_text: str) -> str:
    """Remove street names that merely contain a target locality
    ("rijnsburgerweg" in Oegstgeest) before locality/out-of-scope matching.
    Shared with the export gate."""
    for token in NL_LOCALITY_CONFOUNDERS:
        normalized_text = normalized_text.replace(token, " ")
    return normalized_text


def waterfront_match(
    text: str, streets: list[str] | tuple[str, ...], keywords: list[str] | tuple[str, ...]
) -> str | None:
    """Return the matched whitelist street or water keyword, or None.

    Shared by the scrape filter and the export (which re-gates stored rows
    and surfaces the match as a badge on the site).
    """
    norm = _normalize(text)
    for street in streets:
        if _normalize(street) in norm:
            return street
    for keyword in keywords:
        if _normalize(keyword) in norm:
            return keyword
    return None


def score_listing_nl(
    title: str, address: str, raw_text: str, nl: NlSearchFilters
) -> int:
    """Down-ranking score for surroundings + condition (never a hard filter).

    Whitelist street in the address beats everything; condition keywords and
    a 1960-1985 bouwjaar nudge the sort order. Renovated 70s houses recover
    via condition_plus — the style call stays with the human and the photos.
    """
    address_text = _normalize(f"{title} {address}")
    full_text = _normalize(f"{title} {address} {raw_text}")
    score = 0
    if any(_normalize(s) in address_text for s in nl.waterfront_streets):
        score += 4
    for keyword in nl.condition_plus:
        if _normalize(keyword) in full_text:
            score += 2
    for keyword in nl.condition_minus:
        if _normalize(keyword) in full_text:
            score -= 3
    bouwjaar = extract_bouwjaar(raw_text)
    if bouwjaar is not None and 1960 <= bouwjaar <= 1985:
        score -= 2
    return score


def _nl_ok(item: Listing, nl: NlSearchFilters) -> bool:
    strict_text = _normalize(f"{item.title} {item.address}")
    full_text = _text_blob(item)
    # Locality/out-of-scope decisions must not fire on street names that
    # merely contain a town name; the waterfront gate below keeps the
    # unstripped text (Oegstgeesterweg IS a whitelist street).
    strict_localities = strip_locality_confounders(strict_text)
    full_localities = strip_locality_confounders(full_text)

    # (0) Bare berths/garages/plots: never the house, whatever else matches.
    title_norm = _normalize(item.title)
    if any(_normalize(kw) in title_norm for kw in nl.object_exclude):
        return False
    # (1) Locality, strict: town must appear in title/address.
    if not any(_normalize(loc) in strict_localities for loc in nl.target_localities):
        return False
    # (2) The famous other Valkenburg (Limburg), Oegstgeest, similar traps.
    if any(_normalize(kw) in full_localities for kw in nl.out_of_scope):
        return False
    # (3) Street blocklist — beats everything, even a steiger keyword.
    if any(_normalize(street) in full_text for street in nl.discouraged_streets):
        return False
    # (4) Holiday-home / recreation zoning: hard reject, waterfront or not.
    if any(_normalize(kw) in full_text for kw in nl.recreation_exclude):
        return False
    # (5) Hard waterfront gate: whitelist street OR water keyword.
    if waterfront_match(full_text, nl.waterfront_streets, nl.waterfront_keywords) is None:
        return False
    # (6) Must read like a residential property (Dutch hints; the German
    # PROPERTY_KEYWORDS never match Dutch text).
    if not any(hint in full_text for hint in NL_PROPERTY_KEYWORDS):
        return False
    # (7) Rooms: 4+ (missing value passes, same stance as the CH filter).
    if item.rooms is not None and item.rooms < nl.min_rooms:
        return False
    # (8) No price cap, no area floor by design.
    # (9) Rentals leaking out of buy-only overview pages.
    if "te huur" in full_text and "te koop" not in full_text:
        return False
    return True


def apply_filters(
    listings: list[Listing],
    filters: SearchFilters,
    nl_filters: NlSearchFilters | None = None,
) -> list[Listing]:
    accepted: list[Listing] = []
    for item in listings:
        if item.region == "nl":
            if nl_filters is None or not _nl_ok(item, nl_filters):
                continue
            item.score = score_listing_nl(
                item.title, item.address, item.raw_text, nl_filters
            )
            accepted.append(item)
            continue
        text = _text_blob(item)
        if not _location_match(item, filters):
            continue
        if not _neighborhood_constraints_ok(text, filters):
            continue
        if not _type_hint_ok(text):
            continue
        if not _price_ok(item, filters):
            continue
        if not _rooms_ok(item, filters):
            continue
        if not _area_ok(item, filters):
            continue
        item.score = score_listing(item, filters)
        accepted.append(item)
    return sorted(accepted, key=lambda x: (x.category, -x.score, x.price_chf or 0.0))

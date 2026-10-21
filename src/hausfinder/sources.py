from __future__ import annotations

from dataclasses import dataclass
import json
import re
import sys
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from .config import AppConfig, SourceConfig
from .models import Listing
from .parsing import (
    absolute_url,
    extract_area_sqm,
    extract_area_sqm_nl,
    extract_price_chf,
    extract_price_eur,
    extract_rooms,
    extract_rooms_nl,
    is_pagination_url,
    parse_swiss_number,
    same_host,
)


@dataclass(slots=True)
class _FetchContext:
    headers: dict[str, str]
    timeout: int


def _download(url: str, ctx: _FetchContext) -> str:
    response = requests.get(url, headers=ctx.headers, timeout=ctx.timeout)
    response.raise_for_status()
    return response.text


def _is_listing_link(href: str, keywords: list[str]) -> bool:
    lower = href.lower()
    # Pagination links match the listing keywords and would be fetched as
    # fake detail pages — that is how a Hueppin "IMMOBILIEN KAUFEN" page-2
    # URL once became a Bottighofen map pin with a sold house's address.
    if is_pagination_url(lower):
        return False
    return any(keyword.lower() in lower for keyword in keywords)


def _collect_listing_links(base_url: str, html: str, source: SourceConfig) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if not _is_listing_link(href, source.listing_link_keywords):
            continue
        url = absolute_url(base_url, href)
        if not same_host(base_url, url):
            continue
        if url in seen:
            continue
        seen.add(url)
        links.append(url)
        if len(links) >= source.max_links_per_source:
            break
    return links


def _extract_image_url(detail_url: str, soup: BeautifulSoup) -> str:
    """Title image of a detail page, for the sold-banner OCR check.

    og:image is the most reliable choice: broker sites set it to the lead
    photo — the same image that carries a "Verkauft" banner overlay.
    Casasoft pages (Hueppin, Immokanzlei) set no og:image at all, so fall
    back to the first plausible content <img>: the probe run proved the
    Hueppin 1671998 sold banner exists ONLY in that gallery image (the page
    text never says "verkauft").
    """
    for attrs in ({"property": "og:image"}, {"name": "twitter:image"}):
        meta = soup.find("meta", attrs=attrs)
        content = (meta.get("content") or "").strip() if meta else ""
        if content:
            return absolute_url(detail_url, content)
    for img in soup.find_all("img", src=True):
        src = (img.get("src") or "").strip()
        low = src.lower()
        if not src or low.startswith("data:") or low.endswith(".svg"):
            continue
        if any(token in low for token in ("logo", "icon", "avatar", "placeholder")):
            continue
        return absolute_url(detail_url, src)
    return ""


def _extract_listing_from_detail_page(
    detail_url: str,
    html: str,
    source_name: str,
    category: str,
    region: str = "ch",
) -> Listing:
    soup = BeautifulSoup(html, "html.parser")
    structured = _extract_realadvisor_structured(detail_url, soup, source_name, category)
    if structured:
        return structured

    title = ""
    if soup.title and soup.title.text:
        title = soup.title.text.strip()
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        title = h1.get_text(strip=True)

    text = soup.get_text(separator=" ", strip=True)
    if region == "nl":
        price = extract_price_eur(text)
        area = extract_area_sqm_nl(text)
        rooms = extract_rooms_nl(text)
        address = _extract_address_hint_nl(text)
    else:
        price = extract_price_chf(text)
        area = extract_area_sqm(text)
        rooms = extract_rooms(text)
        address = _extract_address_hint(text)
    return Listing(
        source_name=source_name,
        category=category,
        title=title or "Objekt ohne Titel",
        url=detail_url,
        address=address,
        price_chf=price,
        area_sqm=area,
        rooms=rooms,
        raw_text=text[:10_000],
        image_url=_extract_image_url(detail_url, soup),
        region=region,
        currency="EUR" if region == "nl" else "CHF",
    )


def _extract_realadvisor_structured(
    detail_url: str,
    soup: BeautifulSoup,
    source_name: str,
    category: str,
) -> Listing | None:
    if "realadvisor.ch" not in detail_url:
        return None
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        types = data.get("@type")
        if isinstance(types, str):
            types = [types]
        if not isinstance(types, list) or "Product" not in types:
            continue

        address_data = data.get("address") or {}
        address_parts = [
            address_data.get("streetAddress"),
            address_data.get("postalCode"),
            address_data.get("addressLocality"),
        ]
        address = " ".join(str(part) for part in address_parts if part)
        offers = data.get("offers") or {}
        floor_size = data.get("floorSize") or {}
        text = soup.get_text(separator=" ", strip=True)
        return Listing(
            source_name=source_name,
            category=category,
            title=_realadvisor_title(data, soup),
            url=detail_url,
            address=address,
            price_chf=_as_float(offers.get("price")),
            area_sqm=_as_float(floor_size.get("value")),
            rooms=_as_float(data.get("numberOfRooms")),
            raw_text=text[:10_000],
            image_url=_jsonld_image(detail_url, data)
            or _extract_image_url(detail_url, soup),
        )
    return None


def _jsonld_image(detail_url: str, data: dict) -> str:
    """JSON-LD `image` comes as a string, a list, or an ImageObject dict."""
    image = data.get("image")
    if isinstance(image, list) and image:
        image = image[0]
    if isinstance(image, dict):
        image = image.get("url") or image.get("contentUrl")
    if isinstance(image, str) and image.strip():
        return absolute_url(detail_url, image.strip())
    return ""


def _realadvisor_title(data: dict, soup: BeautifulSoup) -> str:
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)
    return str(data.get("name") or "RealAdvisor Objekt")


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_address_hint(text: str) -> str:
    # Short heuristic: pick the first postcode-like fragment. A Swiss
    # postcode is followed by a capitalized town name; skip year-like
    # numbers (Baujahr, copyright), phone-number fragments, and contact
    # blocks — a broker's own footer address ("Hafenstrasse 48, 8280
    # Kreuzlingen") must not become the property's location.
    tokens = text.split()
    for i, token in enumerate(tokens):
        if not (token.isdigit() and len(token) == 4):
            continue
        if token[:2] in {"19", "20"}:  # a year, never a Thurgau postcode
            continue
        prev = tokens[i - 1].lower().strip(":") if i > 0 else ""
        if prev in {"baujahr", "seit", "jahr", "anno"}:
            continue
        if prev.replace("'", "").isdigit():  # part of a phone number
            continue
        if i + 1 >= len(tokens) or not tokens[i + 1][:1].isupper():
            continue
        left = max(0, i - 3)
        right = min(len(tokens), i + 4)
        window = tokens[left:right]
        if any("@" in t or t.startswith("+41") or t.lower().startswith("tel") for t in window):
            continue
        return " ".join(window)
    return ""


_ADDRESS_RE = re.compile(r"[\wäöüÄÖÜéè .'-]+,\s*\d{4}\s+[A-Za-zäöüÄÖÜéè-]+")

# Dutch postcodes look like "2225 GD Katwijk": four digits + two capitals.
_ADDRESS_NL_RE = re.compile(
    r"([A-Za-z][\w .'\-]{2,40}?\s\d{1,4}[a-zA-Z]?)\s*,?\s*(\d{4}\s?[A-Z]{2})\s+([A-Z][\w-]+)"
)


def _extract_address_hint_nl(text: str) -> str:
    """First street + Dutch postcode + town fragment, skipping contact blocks
    (the broker's own footer must never become the property address)."""
    for match in _ADDRESS_NL_RE.finditer(text):
        window = match.group(0)
        start = max(0, match.start() - 40)
        context = text[start : match.end() + 40]
        if "@" in context or "+31" in context or "tel" in context.lower():
            continue
        return " ".join(window.split())
    return ""

RENT_HINTS = (
    "mietzins", "monatlich", "pro monat", "/mt", "monatsmiete", "nettomiete",
    "zur miete", "zu vermieten",
    # Dutch
    "te huur", "huurprijs", "per maand",
)
BUY_HINTS = (
    "kaufpreis", "zum kauf", "zu verkaufen", "verkaufspreis", "kaufen",
    # Dutch
    "te koop", "vraagprijs", "koopsom", "k.k.", "v.o.n.",
)


def _infer_category(default_category: str, text: str, price: float | None) -> str:
    """Brokers often serve buy and rent objects from one overview page, so the
    category of the pass that discovered a listing is only a guess. Correct it
    from the listing content where the signal is unambiguous."""
    lower = text.lower()
    rent_hit = any(hint in lower for hint in RENT_HINTS)
    buy_hit = any(hint in lower for hint in BUY_HINTS)
    if rent_hit and not buy_hit:
        return "rent"
    if buy_hit and not rent_hit:
        return "buy"
    if price is not None:
        return "buy" if price >= 20_000 else "rent"
    return default_category


def _strip_tracking(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _extract_immostreet_cards(
    search_url: str,
    html: str,
    source_name: str,
    category: str,
) -> list[Listing]:
    """ImmoStreet result pages are server-rendered ImmoScout24 satellites.
    The detail links point at immoscout24.ch, which blocks non-browser
    clients, so all data (including exact coordinates) is taken from the
    result cards instead of detail pages."""
    soup = BeautifulSoup(html, "html.parser")
    listings: list[Listing] = []
    for card in soup.find_all("article", class_="results-item"):
        anchor = card.find("a", href=True)
        if not anchor:
            continue
        url = _strip_tracking(absolute_url(search_url, anchor["href"]))
        # The h2.title in the card is ellipsis-truncated; the image alt
        # carries the full title (with a trailing slide number).
        title = ""
        image_url = ""
        img = card.find("img", alt=True)
        if img:
            title = re.sub(r"\s+\d+$", "", img["alt"]).strip()
            src = (img.get("src") or img.get("data-src") or "").strip()
            if src:
                image_url = absolute_url(search_url, src)
        if not title:
            heading = card.find("h2", class_="title")
            title = heading.get_text(strip=True) if heading else ""
        for gallery in card.find_all("div", class_="gallery-carousel"):
            gallery.decompose()
        text = card.get_text(" ", strip=True)
        if not title:
            title = anchor.get_text(strip=True) or "Objekt ohne Titel"
        location = card.find("div", class_="location")
        if location:
            address = location.get_text(" ", strip=True)
        else:
            address_match = _ADDRESS_RE.search(text)
            address = address_match.group(0).strip() if address_match else ""
        amount = card.find("span", class_="amount")
        price = parse_swiss_number(amount.get_text(strip=True)) if amount else None
        if price is None:
            price = extract_price_chf(text)
        raw_text = text[:10_000]
        # Preserve the card's exact coordinates for the map export.
        geo = card.get("data-map-location")
        if geo:
            try:
                coords = json.loads(geo)
                raw_text += f' {{"latitude": {coords["lat"]}, "longitude": {coords["lng"]}}}'
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
        listings.append(
            Listing(
                source_name=source_name,
                category=category,
                title=title,
                url=url,
                address=address,
                price_chf=price,
                area_sqm=extract_area_sqm(text),
                rooms=extract_rooms(text),
                raw_text=raw_text,
                image_url=image_url,
            )
        )
    return listings


def _fetch_category(
    source: SourceConfig,
    category: str,
    search_url: str,
    ctx: _FetchContext,
    infer_category: bool = False,
) -> Iterable[Listing]:
    search_html = _download(search_url, ctx)
    if "immostreet.ch" in search_url:
        cards = _extract_immostreet_cards(search_url, search_html, source.name, category)
        page = 2
        while cards and len(cards) < source.max_links_per_source and page <= 5:
            page_url = f"{search_url.rstrip('/')}/?pageNum={page}"
            try:
                page_html = _download(page_url, ctx)
            except requests.RequestException:
                break
            more = _extract_immostreet_cards(page_url, page_html, source.name, category)
            if not more or {c.url for c in more} <= {c.url for c in cards}:
                break
            cards.extend(more)
            page += 1
        yield from cards[: source.max_links_per_source]
        return
    detail_links = _collect_listing_links(search_url, search_html, source)
    for link in detail_links:
        try:
            detail_html = _download(link, ctx)
        except requests.RequestException as exc:
            print(
                f"[hausfinder] {source.name}: detail fetch failed for {link}: {exc}",
                file=sys.stderr,
            )
            continue
        listing = _extract_listing_from_detail_page(
            link, detail_html, source.name, category, region=source.region
        )
        if infer_category:
            listing.category = _infer_category(
                category, f"{listing.title} {listing.url} {listing.raw_text}", listing.price_chf
            )
        yield listing


def fetch_all_sources(config: AppConfig) -> list[Listing]:
    contexts = {
        "ch": _FetchContext(
            headers={
                "User-Agent": config.user_agent,
                "Accept-Language": "de-CH,de;q=0.9,en;q=0.8",
            },
            timeout=config.request_timeout_sec,
        ),
        "nl": _FetchContext(
            headers={
                "User-Agent": config.user_agent,
                "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
            },
            timeout=config.request_timeout_sec,
        ),
    }
    listings: list[Listing] = []
    for source in config.sources:
        if not source.enabled:
            continue
        ctx = contexts.get(source.region, contexts["ch"])
        # One overview page serving both categories: fetch once and infer
        # buy/rent per listing instead of storing every object twice.
        dual = bool(source.url_buy) and source.url_buy == source.url_rent
        categories: list[tuple[str, str]] = []
        if source.url_buy:
            categories.append(("buy", source.url_buy))
        if source.url_rent and not dual:
            categories.append(("rent", source.url_rent))
        for category, url in categories:
            try:
                fetched = list(
                    _fetch_category(source, category, url, ctx, infer_category=dual)
                )
            except requests.RequestException as exc:
                print(
                    f"[hausfinder] {source.name}: source fetch failed for {url}: {exc}",
                    file=sys.stderr,
                )
                continue
            # Stamp region/currency post-hoc so per-portal extractors
            # (immostreet, realadvisor) stay untouched.
            for item in fetched:
                item.region = source.region
                item.currency = "EUR" if source.region == "nl" else "CHF"
            listings.extend(fetched)
    return listings

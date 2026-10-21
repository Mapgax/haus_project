"""Push notifications for new scrape findings via ntfy.sh.

Delivery is at-most-once: by the time this runs, the fingerprints have
already been committed to seen_listings, so a failed POST is never retried
on a later run. Note that the fingerprint includes price/title/area/rooms,
so a price change on an existing listing is (intentionally) notified as a
new finding again.
"""

from __future__ import annotations

import json
import os

import requests

from .config import NtfyConfig
from .dashboard import _fmt_num, _fmt_price
from .models import Listing

# Optional click-through target for the push; unset = no click action.
SITE_URL = os.environ.get("HAUS_SITE_URL", "")
# ntfy caps message bodies at 4096 bytes; leave headroom for the trailer.
_DIGEST_BYTE_BUDGET = 3800

_CATEGORY_LABELS = {"buy": "Kauf", "rent": "Miete"}


def _fmt_price_cur(value: float | None, currency: str) -> str:
    if currency == "EUR":
        if value is None:
            return "Auf Anfrage"
        return f"€ {value:,.0f}".replace(",", ".")
    return _fmt_price(value)


def _category_label(item: Listing) -> str:
    label = _CATEGORY_LABELS.get(item.category, item.category)
    # NL findings share the topic with the Kreuzlingen search; the prefix is
    # what tells them apart on the phone.
    return f"NL {label}" if item.region == "nl" else label


def _listing_payload(item: Listing, topic: str) -> dict[str, object]:
    lines = [
        item.title,
        f"{_fmt_num(item.rooms, 'Zi')} · {_fmt_num(item.area_sqm, 'm²')} · "
        f"{item.address or 'Adresse k.A.'}",
        f"Quelle: {item.source_name}",
    ]
    return {
        "topic": topic,
        "title": (
            f"Neues Objekt ({_category_label(item)}): "
            f"{_fmt_price_cur(item.price_chf, item.currency)}"
        ),
        "message": "\n".join(lines),
        "click": item.url,
        "tags": ["house"],
    }


def _digest_payload(items: list[Listing], topic: str) -> dict[str, object]:
    lines: list[str] = []
    used = 0
    shown = 0
    for item in items:
        line = (
            f"• {_fmt_price_cur(item.price_chf, item.currency)} · "
            f"{_fmt_num(item.rooms, 'Zi')} · {item.title[:60]}"
        )
        line_bytes = len(line.encode("utf-8")) + 1
        if used + line_bytes > _DIGEST_BYTE_BUDGET:
            break
        lines.append(line)
        used += line_bytes
        shown += 1
    if shown < len(items):
        lines.append(f"… und {len(items) - shown} weitere")
    payload: dict[str, object] = {
        "topic": topic,
        "title": f"{len(items)} neue Objekte gefunden",
        "message": "\n".join(lines),
        "tags": ["house"],
    }
    if SITE_URL:
        payload["click"] = SITE_URL
    return payload


def send_new_listing_notifications(
    new_items: list[Listing],
    cfg: NtfyConfig,
    *,
    dry_run: bool = False,
    timeout: int = 10,
) -> int:
    """Send one ntfy message per new listing (or one digest). Returns the
    number of messages actually sent; never raises on network errors."""
    if not new_items:
        return 0
    if not cfg.enabled:
        print("ntfy disabled in config - skipping notifications.")
        return 0
    topic = os.environ.get(cfg.topic_env, "").strip()
    if not topic:
        print(f"{cfg.topic_env} not set - skipping notifications.")
        return 0

    if len(new_items) <= cfg.max_individual:
        payloads = [_listing_payload(item, topic) for item in new_items]
    else:
        payloads = [_digest_payload(new_items, topic)]

    if dry_run:
        for payload in payloads:
            # The topic is the only credential of an ntfy topic, and this
            # output is captured into (public) CI artifacts: never print it.
            shown = {**payload, "topic": "<redacted>"}
            print(f"ntfy dry-run: {json.dumps(shown, ensure_ascii=False)}")
        return 0

    sent = 0
    for payload in payloads:
        # Publish as JSON against the server root: unlike the header-based
        # API (Latin-1 only), this survives umlauts in titles/messages.
        try:
            response = requests.post(cfg.server_url, json=payload, timeout=timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            print(f"WARNING: ntfy notification failed ({exc}); skipping the rest.")
            break
        sent += 1
    print(f"Sent {sent}/{len(payloads)} ntfy notification(s) for {len(new_items)} new listing(s).")
    return sent

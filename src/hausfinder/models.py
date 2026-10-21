from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(slots=True)
class Listing:
    source_name: str
    category: str  # "buy" | "rent"
    title: str
    url: str
    address: str = ""
    price_chf: float | None = None
    area_sqm: float | None = None
    rooms: float | None = None
    raw_text: str = ""
    score: int = 0
    image_url: str = ""
    image_status: str | None = None  # 'sold' | 'clear' | 'error' | None
    region: str = "ch"  # 'ch' | 'nl'
    currency: str = "CHF"  # currency of price_chf ('CHF' | 'EUR')
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


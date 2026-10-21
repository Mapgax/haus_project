from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import yaml


@dataclass(slots=True)
class SearchFilters:
    min_rooms: float = 5.0
    min_area_sqm: float = 120.0
    buy_price_limit_chf: float = 3_000_000.0
    rent_price_limit_chf: float = 5_000.0
    target_postcodes: list[str] = field(default_factory=lambda: ["8280"])
    target_localities: list[str] = field(default_factory=lambda: ["kreuzlingen"])
    preferred_neighborhoods: list[str] = field(
        default_factory=lambda: ["zentrum", "egelshofen"]
    )
    discouraged_neighborhoods: list[str] = field(
        default_factory=lambda: ["kurzrickenbach", "bernrain"]
    )
    allow_ambiguous_neighborhood: bool = True
    strict_location_match: bool = True
    location_fallback_to_raw_text: bool = False


# Netherlands search (Katwijk/Rijnsburg/Valkenburg ZH). The constants live
# here so filtering.py and the export share one list without an import cycle.

# Streets along the Oude Rijn whose listings count as waterfront even
# without an explicit steiger/water keyword in the text.
NL_WATERFRONT_STREETS = (
    "overrijn",
    "rijnstraat",
    "uiterdijk",
    "oegstgeesterweg",
    "willibrordlaan",
    "karel de grotelaan",
    "kanaalpad",
)

NL_WATERFRONT_KEYWORDS = (
    "aanlegsteiger",
    "eigen steiger",
    "botenhuis",
    "ligplaats",
    "aan het water",
    "waterkavel",
    "vlonder",
    "steiger",
    "aan de rijn",
    "aan de oude rijn",
)

# Hard reject: recreation-zoned / holiday-home listings. Only
# normally-zoned residential objects are wanted, never a recreatiewoning.
NL_RECREATION_EXCLUDE = (
    "recreatiewoning",
    "vakantiewoning",
    "recreatiebestemming",
    "stacaravan",
    "chalet",
    "niet permanente bewoning",
    "recreatief gebruik",
)

# Hard street blocklist (unwanted surroundings) — beats everything, even a
# waterfront keyword match.
NL_DISCOURAGED_STREETS = (
    "duizendschoon",
    "wingerd",
    "bankijkerweg",
)

# Guard against the famous other Valkenburg (Limburg) on aggregator pages,
# and against Oegstgeest (out of scope by decision; the whitelisted
# Oegstgeesterweg in Rijnsburg survives via the confounder stripping below).
NL_OUT_OF_SCOPE = (
    "valkenburg aan de geul",
    "limburg",
    "oegstgeest",
)

# Street names that CONTAIN a target locality without being in it: the probe
# run let "Rijnsburgerweg 84, Oegstgeest" through because "rijnsburg" matched
# inside the street name. These tokens are stripped from the text before the
# locality/out-of-scope checks (never before the waterfront street check).
# Word boundaries are no alternative: aggregator titles glue postcode+town
# ("…2221CTKatwijk"), which boundaries would wrongly reject.
NL_LOCALITY_CONFOUNDERS = (
    "rijnsburgerweg",
    "oegstgeesterweg",
    "katwijkerweg",
    "valkenburgseweg",
    "valkenburgerweg",
)

# Title-level object blocklist: bare berths/garages/plots pass the waterfront
# gate by nature ("ligplaats" IS a water keyword) but are never the house.
NL_OBJECT_EXCLUDE = (
    "ligplaats",
    "parkeerplaats",
    "garagebox",
    "bouwkavel",
    "berging",
)

# Dutch counterpart of filtering.PROPERTY_KEYWORDS (the German list never
# matches Dutch listing text).
NL_PROPERTY_KEYWORDS = (
    "woning",
    "huis",
    "villa",
    "appartement",
    "bungalow",
    "landhuis",
    "woonboerderij",
)

# Condition/style signals: down-ranking only, never a hard filter — a fully
# renovated 70s house is fine, the score just sorts the photogenic Dutch
# charmers to the top.
NL_CONDITION_PLUS = (
    "gerenoveerd",
    "instapklaar",
    "karakteristiek",
    "jaren 30",
    "jaren '30",
    "gemoderniseerd",
    "hoogwaardig afgewerkt",
)

NL_CONDITION_MINUS = (
    "opknapper",
    "kluswoning",
    "gedateerd",
    "moet gemoderniseerd",
    "achterstallig onderhoud",
)


@dataclass(slots=True)
class NlSearchFilters:
    min_rooms: float = 4.0
    target_localities: list[str] = field(
        default_factory=lambda: ["katwijk", "rijnsburg", "valkenburg"]
    )
    out_of_scope: list[str] = field(default_factory=lambda: list(NL_OUT_OF_SCOPE))
    waterfront_streets: list[str] = field(
        default_factory=lambda: list(NL_WATERFRONT_STREETS)
    )
    waterfront_keywords: list[str] = field(
        default_factory=lambda: list(NL_WATERFRONT_KEYWORDS)
    )
    recreation_exclude: list[str] = field(
        default_factory=lambda: list(NL_RECREATION_EXCLUDE)
    )
    discouraged_streets: list[str] = field(
        default_factory=lambda: list(NL_DISCOURAGED_STREETS)
    )
    object_exclude: list[str] = field(default_factory=lambda: list(NL_OBJECT_EXCLUDE))
    condition_plus: list[str] = field(default_factory=lambda: list(NL_CONDITION_PLUS))
    condition_minus: list[str] = field(default_factory=lambda: list(NL_CONDITION_MINUS))


@dataclass(slots=True)
class SourceConfig:
    name: str
    url_buy: str | None = None
    url_rent: str | None = None
    enabled: bool = True
    listing_link_keywords: list[str] = field(
        default_factory=lambda: ["/immobilien/", "/objekt", "/angebote", "/angebot"]
    )
    max_links_per_source: int = 40
    region: str = "ch"  # 'ch' | 'nl' — selects parsers, filters and currency


@dataclass(slots=True)
class DashboardConfig:
    enabled: bool = True
    open_browser: bool = True
    output_html_path: str = "data/dashboard.html"
    recent_rows: int = 200


@dataclass(slots=True)
class NtfyConfig:
    enabled: bool = True
    server_url: str = "https://ntfy.sh"
    # Name of the env var that holds the topic; the topic itself is the
    # de-facto secret on ntfy.sh and must never be committed.
    topic_env: str = "NTFY_TOPIC"
    # More new listings than this in one run -> a single digest message.
    max_individual: int = 8


@dataclass(slots=True)
class AppConfig:
    user_agent: str
    request_timeout_sec: int
    db_path: str
    filters: SearchFilters
    dashboard: DashboardConfig
    ntfy: NtfyConfig
    sources: list[SourceConfig]
    # Optional parallel filter set for region "nl" sources; when absent,
    # nl-region listings are dropped by apply_filters.
    filters_nl: NlSearchFilters | None = None

def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    filters = SearchFilters(**raw.get("filters", {}))
    filters_nl = (
        NlSearchFilters(**raw["filters_nl"]) if raw.get("filters_nl") else None
    )
    dashboard = DashboardConfig(**raw.get("dashboard", {}))
    # A bare "ntfy:" key parses to None, hence "or {}".
    ntfy = NtfyConfig(**(raw.get("ntfy") or {}))
    sources = [SourceConfig(**item) for item in raw.get("sources", [])]
    if not sources:
        raise ValueError("Config requires at least one source in 'sources'")

    return AppConfig(
        user_agent=raw.get("user_agent", "HausfinderBot/0.1 (+local prototype)"),
        request_timeout_sec=int(raw.get("request_timeout_sec", 20)),
        db_path=raw.get("db_path", "data/seen.sqlite3"),
        filters=filters,
        dashboard=dashboard,
        ntfy=ntfy,
        sources=sources,
        filters_nl=filters_nl,
    )

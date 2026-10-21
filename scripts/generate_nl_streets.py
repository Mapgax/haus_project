"""Generate assets/nl_streets.geojson: real OSM geometry for the NL
waterfront whitelist streets (map overlay on the Katwijk tab).

Uses actual street polylines from Overpass instead of hand-drawn lines —
eyeballed boundaries have been rejected in this project before. The street
list comes from hausfinder.config so overlay and filter can never drift.

Run wherever outbound network is available (locally or in CI):

    PYTHONPATH=src python3 scripts/generate_nl_streets.py

and commit the regenerated assets/nl_streets.geojson. The overlay is
cosmetic; the scrape pipeline does not depend on this file.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import requests

from hausfinder.config import NL_WATERFRONT_STREETS

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Gemeente Katwijk (admin_level 8) covers Katwijk, Rijnsburg and
# Valkenburg ZH; Oegstgeesterweg continues into Oegstgeest, so that
# gemeente is included as well.
QUERY_TEMPLATE = """
[out:json][timeout:90];
(
  area["name"="Katwijk"]["admin_level"="8"]["boundary"="administrative"];
  area["name"="Oegstgeest"]["admin_level"="8"]["boundary"="administrative"];
)->.gemeenten;
way["highway"]["name"~"^({names})$", i](area.gemeenten);
out geom;
"""


def _street_pattern() -> str:
    return "|".join(street.replace(" ", "\\s") for street in NL_WATERFRONT_STREETS)


def fetch_streets() -> dict:
    query = QUERY_TEMPLATE.format(names=_street_pattern())
    response = requests.post(
        OVERPASS_URL,
        data={"data": query},
        headers={"User-Agent": "HausfinderBot/0.1 (+personal property search)"},
        timeout=120,
    )
    response.raise_for_status()
    return response.json()


def to_geojson(overpass: dict) -> dict:
    features = []
    for element in overpass.get("elements", []):
        if element.get("type") != "way" or not element.get("geometry"):
            continue
        coordinates = [[point["lon"], point["lat"]] for point in element["geometry"]]
        features.append(
            {
                "type": "Feature",
                "properties": {"name": element.get("tags", {}).get("name", "")},
                "geometry": {"type": "LineString", "coordinates": coordinates},
            }
        )
    return {"type": "FeatureCollection", "features": features}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="assets/nl_streets.geojson")
    args = parser.parse_args()

    geojson = to_geojson(fetch_streets())
    output = Path(args.output)
    output.write_text(
        json.dumps(geojson, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    names = sorted({f["properties"]["name"] for f in geojson["features"]})
    print(f"Wrote {len(geojson['features'])} street segments to {output}")
    print(f"Streets found: {', '.join(names) or 'NONE — check the name pattern'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Erzeugt strassen-folgende Schul-Einzugsgebiete (Geh-Isochronen) fuer Kreuzlingen.

Idee statt frei gemalter Polygone:
  - Pro Schule wird entlang des ECHTEN Fusswegnetzes (OpenStreetMap) berechnet,
    welche Flaeche in <= OUTER_MIN Minuten zu Fuss erreichbar ist (Isochrone).
  - Weil die Isochrone vom echten Schulpunkt ausgeht, gibt es keinen Versatz mehr.
  - Weil sie dem Strassennetz folgt, ist sie nicht "konisch"/kreisrund,
    sondern haengt an den Strassenzuegen.

Modell "Kern + Grenzgebiet":
  - KERN  (kind="zone")    : Flaeche, die NUR von dieser einen Schule in OUTER_MIN
                             erreichbar ist  -> klare Zuteilung.
  - GRENZE(kind="unclear") : Flaeche, die von >=2 Schulen in OUTER_MIN erreichbar
                             ist -> ueberlappend, Zuteilung unklar.
  - SCHULE(kind="school")  : der Schulpunkt selbst.

Datenfluss / Privacy:
  - EINMALIGER Download des OSM-Fusswegnetzes (nur eine Bounding-Box-Abfrage an
    OpenStreetMap, keine persoenlichen Daten).
  - Berechnung danach komplett lokal.
  - Ergebnis ist ein statisches assets/schools.geojson -> zur Laufzeit der Webseite
    KEIN externer Aufruf.

Ausfuehren:  python3 scripts/generate_school_zones.py
"""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
import osmnx as ox
from shapely.geometry import mapping
from shapely.ops import unary_union

# --- Parameter -------------------------------------------------------------
WALK_SPEED_KMH = 4.5          # angenommene Gehgeschwindigkeit
OUTER_MIN = 9.0               # Minuten -> Aussengrenze der Isochrone
EDGE_BUFFER_M = 55            # Halbe-Block-Puffer um erreichte Strassen (Meter)
NETWORK_DIST_M = 2400         # Radius des heruntergeladenen Netzes um den Zentroid
SIMPLIFY_TOL_M = 12           # Glaetten der Polygone (Meter)

SCHOOLS = [
    {"name": "Schulzentrum Schreiber", "color": "#315c86",
     "address": "Schulstrasse 20, 8280 Kreuzlingen",
     "area": "Zentrum/West, Emmishofen.",
     "lat": 47.6471664, "lon": 9.1785821},
    {"name": "Schulzentrum Wehrli", "color": "#a16010",
     "address": "Marktweg 12, 8280 Kreuzlingen",
     "area": "Zentrum-Ost Richtung Hafen und Seeburgpark.",
     "lat": 47.6489732, "lon": 9.1711706},
    {"name": "Schulzentrum Seetal", "color": "#6b3fa0",
     "address": "Seetalstrasse 80, 8280 Kreuzlingen",
     "area": "Ost / Kurzrickenbach Richtung Bottighofen.",
     "lat": 47.6415493, "lon": 9.1939443},
    {"name": "Schulzentrum Bernegg", "color": "#176f63",
     "address": "Bernrainstrasse 14, 8280 Kreuzlingen",
     "area": "Sued / Sued-West, suedlich der Bahnlinie (inkl. Bernrain).",
     "lat": 47.6459900, "lon": 9.1596302},
]

OUT_PATH = Path(__file__).resolve().parents[1] / "assets" / "schools.geojson"


def build_graph():
    """Laedt das Fusswegnetz einmalig von OSM und projiziert es nach UTM (Meter)."""
    clat = sum(s["lat"] for s in SCHOOLS) / len(SCHOOLS)
    clon = sum(s["lon"] for s in SCHOOLS) / len(SCHOOLS)
    print(f"Lade OSM-Fusswegnetz um ({clat:.4f}, {clon:.4f}), r={NETWORK_DIST_M} m ...")
    G = ox.graph_from_point((clat, clon), dist=NETWORK_DIST_M, network_type="walk")
    G = ox.project_graph(G)  # -> projiziert (Meter), noetig fuer Puffer in Metern
    mpm = WALK_SPEED_KMH * 1000.0 / 60.0
    for _, _, data in G.edges(data=True):
        data["time"] = data["length"] / mpm
    return G


def isochrone_polygon(G, lat, lon):
    """Erreichbare Flaeche (<= OUTER_MIN) als strassen-folgendes Polygon."""
    import geopandas as gpd
    from shapely.geometry import Point

    # nearest_nodes erwartet Koordinaten im CRS des Graphen -> Schulpunkt projizieren
    pt = gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326").to_crs(G.graph["crs"]).iloc[0]
    center = ox.distance.nearest_nodes(G, X=pt.x, Y=pt.y)

    sub = nx.ego_graph(G, center, radius=OUTER_MIN, distance="time")
    edges = ox.graph_to_gdfs(sub, nodes=False, edges=True)
    area = unary_union(list(edges["geometry"])).buffer(EDGE_BUFFER_M)
    area = area.buffer(-EDGE_BUFFER_M * 0.35).buffer(EDGE_BUFFER_M * 0.35)  # Loecher schliessen
    return area.simplify(SIMPLIFY_TOL_M)


def to_wgs84(geom, crs):
    import geopandas as gpd
    return gpd.GeoSeries([geom], crs=crs).to_crs("EPSG:4326").iloc[0]


def main():
    G = build_graph()
    crs = G.graph["crs"]

    outers = []
    for s in SCHOOLS:
        print(f"  Isochrone: {s['name']}")
        outers.append(isochrone_polygon(G, s["lat"], s["lon"]))

    features = []

    # Kerne: exklusiv von genau einer Schule erreichbar
    for s, poly in zip(SCHOOLS, outers):
        others = unary_union([p for p in outers if p is not poly])
        core = poly.difference(others).buffer(0)
        if core.is_empty:
            continue
        features.append({
            "type": "Feature",
            "properties": {
                "name": s["name"], "kind": "zone", "color": s["color"],
                "area": s["area"],
            },
            "geometry": mapping(to_wgs84(core, crs)),
        })

    # Grenzgebiet: von >=2 Schulen erreichbar (paarweise Schnitte vereinigt)
    overlap_parts = []
    for i in range(len(outers)):
        for j in range(i + 1, len(outers)):
            inter = outers[i].intersection(outers[j])
            if not inter.is_empty:
                overlap_parts.append(inter)
    if overlap_parts:
        overlap = unary_union(overlap_parts).buffer(0).simplify(SIMPLIFY_TOL_M)
        features.append({
            "type": "Feature",
            "properties": {
                "name": "Grenzgebiet – Zuteilung unklar",
                "kind": "unclear", "color": "#67717f",
                "area": "Von zwei oder mehr Schulhaeusern in rund "
                        f"{OUTER_MIN:.0f} Gehminuten erreichbar - die Schule Kreuzlingen "
                        "teilt solche Strassenzuege je nach Jahrgang wechselnd zu.",
            },
            "geometry": mapping(to_wgs84(overlap, crs)),
        })

    # Schulpunkte
    for s in SCHOOLS:
        features.append({
            "type": "Feature",
            "properties": {
                "name": s["name"], "kind": "school", "color": s["color"],
                "address": s["address"], "area": s["area"],
            },
            "geometry": {"type": "Point", "coordinates": [s["lon"], s["lat"]]},
        })

    fc = {
        "type": "FeatureCollection",
        "note": (
            "Approximative Primar-Einzugsgebiete Kreuzlingen auf Basis von "
            f"Geh-Isochronen (<= {OUTER_MIN:.0f} min, {WALK_SPEED_KMH} km/h) entlang des "
            "OSM-Fusswegnetzes. KEINE offiziellen Grenzen. Kern = nur von einer Schule "
            "erreichbar; Grenzgebiet = von mehreren Schulen erreichbar."
        ),
        "features": features,
    }

    OUT_PATH.write_text(json.dumps(fc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Geschrieben: {OUT_PATH}  ({len(features)} Features)")


if __name__ == "__main__":
    main()

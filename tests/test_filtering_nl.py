"""Tests for the hard NL gates (waterfront, blocklist, recreation) and the
surroundings/condition down-ranking score.

The NL search is buy-only with no price cap; what protects the result list
is the hard waterfront gate, the street blocklist and the recreation
exclusion — pinned here in precedence order.

Run with: PYTHONPATH=src python3 -m unittest discover tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hausfinder.config import NlSearchFilters, SearchFilters
from hausfinder.filtering import apply_filters, score_listing_nl, waterfront_match
from hausfinder.models import Listing


def _nl_listing(**overrides) -> Listing:
    values = {
        "source_name": "Van der Meer Makelaars",
        "category": "buy",
        "title": "Vrijstaande woning in Rijnsburg",
        "url": "https://example.nl/aanbod/huis-1",
        "address": "Overrijn 12, 2231 CB Rijnsburg",
        "price_chf": 895_000.0,
        "rooms": 5.0,
        "region": "nl",
        "currency": "EUR",
    }
    values.update(overrides)
    return Listing(**values)


def _apply(items: list[Listing]) -> list[Listing]:
    return apply_filters(items, SearchFilters(), NlSearchFilters())


class WaterfrontGateTest(unittest.TestCase):
    def test_whitelist_street_passes_without_keyword(self) -> None:
        self.assertEqual(len(_apply([_nl_listing()])), 1)

    def test_added_street_karel_de_grotelaan_passes(self) -> None:
        item = _nl_listing(address="Karel de Grotelaan 8, Rijnsburg")
        self.assertEqual(len(_apply([item])), 1)

    def test_keyword_passes_without_whitelist_street(self) -> None:
        item = _nl_listing(
            address="Dorpsstraat 4, Katwijk",
            raw_text="woning met eigen aanlegsteiger aan het water",
        )
        self.assertEqual(len(_apply([item])), 1)

    def test_neither_street_nor_keyword_rejected(self) -> None:
        item = _nl_listing(address="Dorpsstraat 4, Katwijk", raw_text="mooie tuin")
        self.assertEqual(_apply([item]), [])

    def test_blocklist_street_beats_water_keyword(self) -> None:
        item = _nl_listing(
            address="Wingerd 3, Rijnsburg",
            raw_text="eigen steiger aan het water",
        )
        self.assertEqual(_apply([item]), [])

    def test_recreation_zoning_beats_water_keyword(self) -> None:
        item = _nl_listing(raw_text="recreatiewoning met eigen steiger")
        self.assertEqual(_apply([item]), [])


class NlGateTest(unittest.TestCase):
    def test_three_rooms_rejected_four_pass(self) -> None:
        self.assertEqual(_apply([_nl_listing(rooms=3.0)]), [])
        self.assertEqual(len(_apply([_nl_listing(rooms=4.0)])), 1)

    def test_no_price_cap(self) -> None:
        self.assertEqual(len(_apply([_nl_listing(price_chf=2_500_000.0)])), 1)

    def test_wrong_valkenburg_rejected(self) -> None:
        item = _nl_listing(
            title="Woning in Valkenburg",
            address="Bergstraat 2, Valkenburg aan de Geul",
            raw_text="prachtig huis in Limburg aan het water",
        )
        self.assertEqual(_apply([item]), [])

    def test_rijnsburgerweg_in_oegstgeest_rejected(self) -> None:
        # Probe-run leak: "rijnsburg" matched inside the street name.
        item = _nl_listing(
            title="Woning aan de Rijnsburgerweg",
            address="Rijnsburgerweg 84, 2341 AB Oegstgeest",
            raw_text="woning aan het water, 6 kamers",
        )
        self.assertEqual(_apply([item]), [])

    def test_oegstgeesterweg_in_rijnsburg_still_passes(self) -> None:
        # The whitelisted street contains "oegstgeest" — the confounder
        # stripping must not kill it via the out-of-scope gate.
        item = _nl_listing(
            title="Woning aan de Oegstgeesterweg",
            address="Oegstgeesterweg 12, 2231 AT Rijnsburg",
            raw_text="",
        )
        self.assertEqual(len(_apply([item])), 1)

    def test_bare_berth_listing_rejected(self) -> None:
        # Probe-run leak: a berth-only ad passes the water gate by nature.
        item = _nl_listing(
            title="Noordwijkerweg - Ligplaats 50",
            address="Noordwijkerweg, 2231 NJ Rijnsburg",
            raw_text="ligplaats voor uw boot, koopwoning menu",
            rooms=None,
        )
        self.assertEqual(_apply([item]), [])

    def test_locality_outside_target_rejected(self) -> None:
        item = _nl_listing(address="Overrijn 1, 2311 XY Leiden", title="Woning in Leiden")
        self.assertEqual(_apply([item]), [])

    def test_rental_leak_rejected(self) -> None:
        item = _nl_listing(raw_text="deze woning is te huur per maand")
        self.assertEqual(_apply([item]), [])

    def test_nl_rows_dropped_without_nl_filters(self) -> None:
        result = apply_filters([_nl_listing()], SearchFilters(), None)
        self.assertEqual(result, [])

    def test_ch_listing_unaffected_by_nl_gates(self) -> None:
        ch = Listing(
            source_name="RealAdvisor",
            category="buy",
            title="Einfamilienhaus in Kreuzlingen mit Garten",
            url="https://example.ch/haus",
            address="Hauptstrasse 1, 8280 Kreuzlingen",
            price_chf=1_250_000.0,
            rooms=5.5,
            area_sqm=160.0,
        )
        result = _apply([ch])
        self.assertEqual([item.url for item in result], ["https://example.ch/haus"])


class ScoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.nl = NlSearchFilters()

    def test_preferred_street_boost(self) -> None:
        boosted = score_listing_nl("Woning", "Overrijn 12, Rijnsburg", "", self.nl)
        neutral = score_listing_nl("Woning", "Dorpsstraat 4, Katwijk", "", self.nl)
        self.assertGreater(boosted, neutral)

    def test_opknapper_ranks_below_neutral(self) -> None:
        fixer = score_listing_nl("Woning", "", "echte opknapper", self.nl)
        self.assertLess(fixer, 0)

    def test_seventies_bouwjaar_penalty(self) -> None:
        seventies = score_listing_nl("Woning", "", "bouwjaar 1972", self.nl)
        self.assertEqual(seventies, -2)

    def test_renovation_compensates_seventies(self) -> None:
        renovated = score_listing_nl(
            "Woning", "", "bouwjaar 1972, volledig gerenoveerd", self.nl
        )
        self.assertEqual(renovated, 0)

    def test_condition_plus_boost(self) -> None:
        self.assertEqual(
            score_listing_nl("Woning", "", "instapklaar en karakteristiek", self.nl), 4
        )


class WaterfrontMatchTest(unittest.TestCase):
    def test_returns_matched_street(self) -> None:
        nl = NlSearchFilters()
        self.assertEqual(
            waterfront_match("huis aan de Uiterdijk", nl.waterfront_streets, nl.waterfront_keywords),
            "uiterdijk",
        )

    def test_returns_matched_keyword(self) -> None:
        nl = NlSearchFilters()
        self.assertEqual(
            waterfront_match("woning met ligplaats", nl.waterfront_streets, nl.waterfront_keywords),
            "ligplaats",
        )

    def test_no_match(self) -> None:
        nl = NlSearchFilters()
        self.assertIsNone(
            waterfront_match("woning met tuin", nl.waterfront_streets, nl.waterfront_keywords)
        )


if __name__ == "__main__":
    unittest.main()

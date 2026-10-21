"""Tests for the region split in the Vercel export: NL rows must pass their
own gates (Dutch keywords, waterfront, no German location filters), get EUR
labels and NL coordinates, and never contaminate the CH price stats.

Run with: PYTHONPATH=src python3 -m unittest discover tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import export_vercel_data as evd


def _nl_row(**overrides) -> dict:
    row = {
        "fp": "fp-nl-1",
        "fetched_at_utc": "2026-07-12T10:00:00+00:00",
        "first_seen_utc": "2026-07-01T09:00:00+00:00",
        "source_name": "Van der Meer Makelaars",
        "category": "buy",
        "region": "nl",
        "currency": "EUR",
        "title": "Vrijstaande woning aan de Overrijn",
        "url": "https://example.nl/aanbod/overrijn-12",
        "address": "Overrijn 12, 2231 CB Rijnsburg",
        "raw_text": "eigen aanlegsteiger, bouwjaar 1972, 5 kamers, gerenoveerd",
        "price_chf": 895_000.0,
        "area_sqm": 140.0,
        "rooms": 5.0,
        "score": 0,
        "image_status": None,
    }
    row.update(overrides)
    return row


def _ch_row(**overrides) -> dict:
    row = {
        "fp": "fp-ch-1",
        "fetched_at_utc": "2026-07-12T10:00:00+00:00",
        "first_seen_utc": "2026-07-01T09:00:00+00:00",
        "source_name": "RealAdvisor",
        "category": "buy",
        "region": "ch",
        "currency": "CHF",
        "title": "Einfamilienhaus in Kreuzlingen",
        "url": "https://example.ch/haus-1",
        "address": "Hauptstrasse 1, 8280 Kreuzlingen",
        "raw_text": "grosszügiges Haus mit Garten",
        "price_chf": 1_250_000.0,
        "area_sqm": 160.0,
        "rooms": 5.5,
        "score": 0,
        "image_status": None,
    }
    row.update(overrides)
    return row


class IsCandidateNlTest(unittest.TestCase):
    def test_waterfront_row_passes(self) -> None:
        self.assertTrue(evd._is_candidate(_nl_row()))

    def test_german_residential_gate_does_not_kill_dutch_text(self) -> None:
        # "woning" is not in the German RESIDENTIAL_KEYWORDS; the NL branch
        # must use the Dutch list.
        row = _nl_row(title="Karakteristieke woning", raw_text="eigen steiger, 5 kamers")
        self.assertTrue(evd._is_candidate(row))

    def test_without_waterfront_rejected(self) -> None:
        # url overridden too: a street name in the listing slug counts as a
        # waterfront signal, so the default fixture url would pass the gate.
        row = _nl_row(
            title="Vrijstaande woning in Katwijk",
            address="Dorpsstraat 4, Katwijk",
            raw_text="mooie tuin, 5 kamers",
            url="https://example.nl/aanbod/dorpsstraat-4",
        )
        self.assertFalse(evd._is_candidate(row))

    def test_blocklist_street_rejected(self) -> None:
        row = _nl_row(address="Wingerd 3, Rijnsburg", raw_text="eigen steiger")
        self.assertFalse(evd._is_candidate(row))

    def test_recreation_rejected(self) -> None:
        row = _nl_row(raw_text="recreatiewoning met aanlegsteiger")
        self.assertFalse(evd._is_candidate(row))

    def test_verkocht_status_rejected(self) -> None:
        row = _nl_row(title="VERKOCHT: Vrijstaande woning aan de Overrijn")
        self.assertFalse(evd._is_candidate(row))

    def test_sold_image_status_rejected_in_both_regions(self) -> None:
        self.assertFalse(evd._is_candidate(_nl_row(image_status="sold")))
        self.assertFalse(evd._is_candidate(_ch_row(image_status="sold")))

    def test_missing_area_passes_nl_only(self) -> None:
        # NL has no area floor by design; the CH gate still requires it.
        self.assertTrue(evd._is_candidate(_nl_row(area_sqm=None)))
        self.assertFalse(evd._is_candidate(_ch_row(area_sqm=None)))

    def test_three_rooms_rejected(self) -> None:
        self.assertFalse(evd._is_candidate(_nl_row(rooms=3.0)))

    def test_wrong_valkenburg_rejected(self) -> None:
        row = _nl_row(
            title="Woning met steiger in Valkenburg",
            address="Bergstraat 2, Valkenburg aan de Geul",
            raw_text="prachtig gelegen in Limburg, ligplaats",
        )
        self.assertFalse(evd._is_candidate(row))

    def test_rijnsburgerweg_in_oegstgeest_rejected(self) -> None:
        row = _nl_row(
            title="Woning aan de Rijnsburgerweg",
            address="Rijnsburgerweg 84, 2341 AB Oegstgeest",
            raw_text="woning aan het water, 6 kamers",
            url="https://example.nl/koopwoning/rijnsburgerweg-84",
        )
        self.assertFalse(evd._is_candidate(row))

    def test_oegstgeesterweg_in_rijnsburg_passes(self) -> None:
        row = _nl_row(
            title="Woning aan de Oegstgeesterweg",
            address="Oegstgeesterweg 12, 2231 AT Rijnsburg",
            raw_text="mooie woning, 5 kamers",
            url="https://example.nl/koopwoning/oegstgeesterweg-12",
        )
        self.assertTrue(evd._is_candidate(row))

    def test_bare_berth_listing_rejected(self) -> None:
        row = _nl_row(
            title="Noordwijkerweg - Ligplaats 50",
            address="Noordwijkerweg, 2231 NJ Rijnsburg",
            raw_text="ligplaats voor uw boot",
            rooms=None,
            url="https://example.nl/koopwoning/ligplaats-50",
        )
        self.assertFalse(evd._is_candidate(row))


@mock.patch.object(evd, "_pdok_lookup", return_value=None)
@mock.patch.object(evd, "_listing_status_from_url", return_value=(True, None))
class RowToListingTest(unittest.TestCase):
    def test_nl_fields_and_eur_label(self, status: mock.Mock, pdok: mock.Mock) -> None:
        listing = evd._row_to_listing(_nl_row())
        self.assertEqual(listing["region"], "nl")
        self.assertEqual(listing["currency"], "EUR")
        self.assertEqual(listing["priceLabel"], "€ 895.000")
        self.assertEqual(listing["waterfront"], "overrijn")
        self.assertTrue(listing["preferredStreet"])
        self.assertEqual(listing["bouwjaar"], 1972)
        self.assertIn("gerenoveerd", listing["conditionPlus"])
        self.assertEqual(listing["firstSeen"], "2026-07-01T09:00:00+00:00")
        # +4 street, +2 gerenoveerd, -2 bouwjaar 1972
        self.assertEqual(listing["nlScore"], 4)

    def test_nl_fallback_is_rijnsburg_never_kreuzlingen(
        self, status: mock.Mock, pdok: mock.Mock
    ) -> None:
        row = _nl_row(address="", raw_text="eigen aanlegsteiger, 5 kamers, Rijnsburg woning")
        listing = evd._row_to_listing(row)
        self.assertGreater(listing["lat"], 51.5)
        self.assertLess(listing["lon"], 7.5)
        self.assertNotEqual(listing["locationLabel"], "Kreuzlingen")

    def test_pdok_result_wins_over_locality_hint(
        self, status: mock.Mock, pdok: mock.Mock
    ) -> None:
        pdok.return_value = (52.1955, 4.4231, "pdok-adres", "Overrijn 12, Rijnsburg")
        listing = evd._row_to_listing(_nl_row())
        self.assertEqual(listing["geoPrecision"], "pdok-adres")
        self.assertAlmostEqual(listing["lat"], 52.1955)

    def test_ch_row_keeps_chf_label(self, status: mock.Mock, pdok: mock.Mock) -> None:
        listing = evd._row_to_listing(_ch_row())
        self.assertEqual(listing["priceLabel"], "CHF 1'250'000")
        self.assertEqual(listing["region"], "ch")
        self.assertNotIn("waterfront", listing)

    def test_nl_broker_footer_address_cleaned(
        self, status: mock.Mock, pdok: mock.Mock
    ) -> None:
        # Dutch contact blocks (+31 ...) must be emptied like Swiss ones.
        self.assertEqual(evd._clean_address("info kantoor +31 71 123 45 67"), "")


class PriceStatsRegionTest(unittest.TestCase):
    def _listing(self, group: str, ppsqm: int, ident: str) -> dict:
        return {
            "id": ident,
            "category": "buy",
            "region": "nl" if group == "nl" else "ch",
            "pricePerSqm": ppsqm,
        }

    def test_nl_prices_do_not_move_ch_median(self) -> None:
        ch = [self._listing("buy", 7_000 + i, f"ch{i}") for i in range(5)]
        nl = [self._listing("nl", 4_000 + i, f"nl{i}") for i in range(5)]
        stats = evd._compute_price_stats(ch + nl)
        self.assertEqual(stats["buy"]["medianPpsqm"], 7_002)
        self.assertEqual(stats["nl"]["medianPpsqm"], 4_002)

    def test_nl_items_never_get_valuation(self) -> None:
        nl = [self._listing("nl", 4_000 + i, f"nl{i}") for i in range(6)]
        evd._compute_price_stats(nl)
        self.assertTrue(all(item["priceValuation"] is None for item in nl))

    def test_ch_items_keep_valuation(self) -> None:
        ch = [self._listing("buy", 7_000 + i * 100, f"ch{i}") for i in range(6)]
        evd._compute_price_stats(ch)
        self.assertTrue(any(item["priceValuation"] is not None for item in ch))


if __name__ == "__main__":
    unittest.main()

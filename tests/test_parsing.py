"""Regression tests for the Swiss listing-text extractors.

These formats all appear on real sources and previously failed silently:
"5.5-Zimmer-Wohnung" (hyphen), "5½"/"5 1/2" (half rooms — "5 1/2 Zimmer"
even parsed as 2 rooms and got filtered out), "93 m 2" (BeautifulSoup
splits m<sup>2</sup>), "850'000 CHF" (price after the currency), and
typographic apostrophes as thousands separators.

Run with: PYTHONPATH=src .venv/bin/python3 -m unittest discover tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hausfinder.parsing import (
    extract_area_sqm,
    extract_price_chf,
    extract_rooms,
    same_host,
)


class PriceTest(unittest.TestCase):
    def test_currency_before_number(self) -> None:
        self.assertEqual(extract_price_chf("Kaufpreis CHF 2'450'000.-"), 2_450_000.0)

    def test_currency_after_number(self) -> None:
        self.assertEqual(extract_price_chf("850'000 CHF"), 850_000.0)

    def test_card_text_does_not_mistake_rooms_for_price(self) -> None:
        # ImmoStreet card text: price first, then room count after "CHF".
        self.assertEqual(extract_price_chf("850'000 CHF 3.5 Zimmer 93 m 2"), 850_000.0)

    def test_typographic_apostrophe(self) -> None:
        self.assertEqual(extract_price_chf("CHF 1’250’000"), 1_250_000.0)

    def test_german_thousands_grouping(self) -> None:
        self.assertEqual(extract_price_chf("CHF 2.450.000"), 2_450_000.0)

    def test_no_price(self) -> None:
        self.assertIsNone(extract_price_chf("Preis auf Anfrage"))

    def test_ignores_search_filter_dropdown(self) -> None:
        # Retronova renders a min/max price dropdown on every detail page.
        self.assertIsNone(
            extract_price_chf(
                "Min Preis CHF 1'000 CHF 5'000 CHF 10'000 CHF 50'000 "
                "CHF 100'000 Max Preis CHF 1'000 CHF 5'000 CHF 10'000"
            )
        )

    def test_real_price_survives_dropdown_removal(self) -> None:
        self.assertEqual(
            extract_price_chf(
                "Min Preis CHF 1'000 CHF 5'000 CHF 10'000 "
                "Kaufpreis CHF 1'250'000 Wohnfläche 140 m²"
            ),
            1_250_000.0,
        )


class RoomsTest(unittest.TestCase):
    def test_hyphenated_compound(self) -> None:
        self.assertEqual(extract_rooms("5.5-Zimmer-Wohnung"), 5.5)

    def test_unicode_half(self) -> None:
        self.assertEqual(extract_rooms("5½ Zimmer"), 5.5)

    def test_fraction_half(self) -> None:
        # Used to match "2 Zimmer" and report 2 rooms.
        self.assertEqual(extract_rooms("5 1/2 Zimmer"), 5.5)

    def test_abbreviation(self) -> None:
        self.assertEqual(extract_rooms("4,5 Zi."), 4.5)

    def test_does_not_match_zimmerei(self) -> None:
        self.assertIsNone(extract_rooms("Zimmerei Meier AG"))


class AreaTest(unittest.TestCase):
    def test_superscript_split_by_soup(self) -> None:
        self.assertEqual(extract_area_sqm("93 m 2"), 93.0)

    def test_prefers_labeled_wohnflaeche(self) -> None:
        self.assertEqual(
            extract_area_sqm("Grundstück 800 m², Wohnfläche 145 m²"), 145.0
        )

    def test_plain(self) -> None:
        self.assertEqual(extract_area_sqm("120m2"), 120.0)


class HostTest(unittest.TestCase):
    def test_www_prefix_is_equivalent(self) -> None:
        # bocelli-immobilien.ch links to www.bocelli-immobilien.ch; the
        # old exact-netloc comparison dropped every listing link.
        self.assertTrue(
            same_host(
                "https://bocelli-immobilien.ch/immobilien/kauf/",
                "https://www.bocelli-immobilien.ch/detailseite/365-x/",
            )
        )

    def test_different_hosts(self) -> None:
        self.assertFalse(
            same_host("https://a.example.ch/x", "https://b.example.ch/y")
        )


if __name__ == "__main__":
    unittest.main()

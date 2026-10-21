"""Regression tests for the Dutch (NL) listing-text extractors.

The central trap: Dutch prices group thousands with dots ("€ 750.000"),
which parse_swiss_number reads as 750.0 — three orders of magnitude off.
These tests pin that the NL parsers exist precisely so that never happens.

Run with: PYTHONPATH=src python3 -m unittest discover tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hausfinder.parsing import (
    extract_area_sqm_nl,
    extract_bouwjaar,
    extract_price_eur,
    extract_rooms_nl,
    parse_dutch_number,
    parse_swiss_number,
)


class DutchNumberTest(unittest.TestCase):
    def test_dots_are_thousands_separators(self) -> None:
        self.assertEqual(parse_dutch_number("750.000"), 750_000.0)

    def test_swiss_parser_would_get_this_wrong(self) -> None:
        # The reason parse_dutch_number exists: the Swiss parser reads the
        # dot as a decimal mark. If this ever changes, the NL parsers can
        # be consolidated — until then they must stay separate.
        self.assertEqual(parse_swiss_number("750.000"), 750.0)

    def test_comma_is_decimal_mark(self) -> None:
        self.assertEqual(parse_dutch_number("1.234,56"), 1234.56)

    def test_garbage_returns_none(self) -> None:
        self.assertIsNone(parse_dutch_number(""))
        self.assertIsNone(parse_dutch_number("prijs"))


class EurPriceTest(unittest.TestCase):
    def test_kosten_koper_suffix(self) -> None:
        self.assertEqual(extract_price_eur("€ 750.000 k.k."), 750_000.0)

    def test_vraagprijs_von(self) -> None:
        self.assertEqual(
            extract_price_eur("Vraagprijs € 1.250.000 v.o.n."), 1_250_000.0
        )

    def test_price_after_currency_symbol_missing(self) -> None:
        self.assertIsNone(extract_price_eur("vraagprijs op aanvraag"))

    def test_amount_before_symbol(self) -> None:
        self.assertEqual(extract_price_eur("895.000 € kosten koper"), 895_000.0)

    def test_dropdown_run_is_ignored(self) -> None:
        text = "Prijs € 100.000 € 200.000 € 300.000 € 400.000 filter"
        self.assertIsNone(extract_price_eur(text))


class RoomsNlTest(unittest.TestCase):
    def test_kamers(self) -> None:
        self.assertEqual(extract_rooms_nl("royale woning met 5 kamers"), 5.0)

    def test_kamerwoning_compound(self) -> None:
        self.assertEqual(extract_rooms_nl("ruime 5-kamerwoning"), 5.0)

    def test_kamerappartement_compound(self) -> None:
        self.assertEqual(extract_rooms_nl("4-kamerappartement"), 4.0)

    def test_slaapkamers_add_living_room(self) -> None:
        # Dutch convention: kamers ≈ slaapkamers + woonkamer.
        self.assertEqual(extract_rooms_nl("4 slaapkamers en 2 badkamers"), 5.0)

    def test_kamers_preferred_over_slaapkamers(self) -> None:
        self.assertEqual(extract_rooms_nl("6 kamers waarvan 4 slaapkamers"), 6.0)

    def test_no_rooms(self) -> None:
        self.assertIsNone(extract_rooms_nl("kamerbreed tapijt"))


class AreaNlTest(unittest.TestCase):
    def test_woonoppervlakte_beats_perceel(self) -> None:
        text = "perceel 300 m² … woonoppervlakte 120 m²"
        self.assertEqual(extract_area_sqm_nl(text), 120.0)

    def test_plain_area(self) -> None:
        self.assertEqual(extract_area_sqm_nl("wonen op 145 m²"), 145.0)


class BouwjaarTest(unittest.TestCase):
    def test_labeled(self) -> None:
        self.assertEqual(extract_bouwjaar("Bouwjaar: 1934, gerenoveerd"), 1934)

    def test_without_colon(self) -> None:
        self.assertEqual(extract_bouwjaar("bouwjaar 2021"), 2021)

    def test_postcode_is_not_a_bouwjaar(self) -> None:
        self.assertIsNone(extract_bouwjaar("2231 CB Rijnsburg, vraagprijs 1985"))

    def test_implausible_year_rejected(self) -> None:
        self.assertIsNone(extract_bouwjaar("bouwjaar 1750"))


if __name__ == "__main__":
    unittest.main()

"""Tests for the median-based price valuation in the static-site export.

The verdict thresholds live in hausfinder.dashboard (ratio <= 0.90 cheap,
>= 1.10 expensive, both inclusive); these tests pin that the export applies
them per category, skips categories with too few priced listings, and never
lets rent outliers skew the buy median.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import export_vercel_data as evd


def _item(category: str = "buy", ppsqm: float | None = None) -> dict:
    return {"category": category, "pricePerSqm": ppsqm}


class ComputePriceStatsTest(unittest.TestCase):
    def test_median_and_verdicts_per_listing(self) -> None:
        listings = [
            _item(ppsqm=5000),
            _item(ppsqm=5500),
            _item(ppsqm=6000),
            _item(ppsqm=6500),
            _item(ppsqm=7000),
            _item(ppsqm=None),
        ]
        stats = evd._compute_price_stats(listings)
        self.assertEqual(stats["buy"], {"medianPpsqm": 6000, "count": 5})
        verdicts = [item["priceValuation"] for item in listings]
        # 5000/6000 = 0.833 cheap; 7000/6000 = 1.167 expensive; no price -> None
        self.assertEqual(
            verdicts, ["guenstig", "mittel", "mittel", "mittel", "teuer", None]
        )

    def test_thresholds_are_inclusive(self) -> None:
        # Median of the five values is 6000; 5400 == 0.90x and 6600 == 1.10x.
        listings = [
            _item(ppsqm=5400),
            _item(ppsqm=5900),
            _item(ppsqm=6000),
            _item(ppsqm=6100),
            _item(ppsqm=6600),
        ]
        evd._compute_price_stats(listings)
        self.assertEqual(listings[0]["priceValuation"], "guenstig")
        self.assertEqual(listings[-1]["priceValuation"], "teuer")

    def test_small_category_gets_no_stats_or_verdicts(self) -> None:
        listings = [_item(ppsqm=v) for v in (5000, 6000, 7000, 8000)]
        stats = evd._compute_price_stats(listings)
        self.assertEqual(stats, {})
        self.assertTrue(all(item["priceValuation"] is None for item in listings))

    def test_rent_outlier_cannot_skew_buy_median(self) -> None:
        buy = [_item(ppsqm=v) for v in (5000, 5500, 6000, 6500, 7000)]
        rent = [_item(category="rent", ppsqm=25)]
        stats = evd._compute_price_stats(buy + rent)
        self.assertEqual(stats["buy"]["medianPpsqm"], 6000)
        self.assertNotIn("rent", stats)
        self.assertIsNone(rent[0]["priceValuation"])
        self.assertEqual(buy[2]["priceValuation"], "mittel")


if __name__ == "__main__":
    unittest.main()

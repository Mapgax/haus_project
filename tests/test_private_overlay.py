"""Tests for the private-settings overlay (api/_overlay.py, api/listings.py).

The overlay must (1) change nothing when there are no settings, (2) reproduce the
exporter's distance and label arithmetic exactly, and (3) never raise, whatever
the settings table contains.
"""

from __future__ import annotations

import copy
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "scripts"))

import _overlay  # noqa: E402
import export_vercel_data as evd  # noqa: E402

PUBLIC = json.loads((ROOT / "assets" / "listings.json").read_text(encoding="utf-8"))
OTHER_REF = {"label": "Testpunkt", "lat": 47.50, "lon": 9.10}


class NoSettingsTest(unittest.TestCase):
    def test_empty_settings_return_an_equal_copy(self) -> None:
        for settings in (None, {}, [], "x", {"unknown": 1}):
            out = _overlay.apply_overlay(PUBLIC, settings)
            self.assertEqual(out, PUBLIC)
            self.assertIsNot(out, PUBLIC)

    def test_input_is_never_mutated(self) -> None:
        before = copy.deepcopy(PUBLIC)
        _overlay.apply_overlay(PUBLIC, {"ref_ch": OTHER_REF, "buy_limit_chf": 1_000_000})
        self.assertEqual(PUBLIC, before)


class ReferencePointTest(unittest.TestCase):
    def test_default_reference_reproduces_the_committed_distances(self) -> None:
        ref = dict(PUBLIC["references"]["ch"])
        out = _overlay.apply_overlay(PUBLIC, {"ref_ch": ref})
        for new, old in zip(out["listings"], PUBLIC["listings"]):
            self.assertEqual(new["distanceKm"], old["distanceKm"], old["id"])

    def test_matches_the_exporter_for_a_different_reference(self) -> None:
        out = _overlay.apply_overlay(PUBLIC, {"ref_ch": OTHER_REF})
        with mock.patch.dict(evd.REFERENCES, {"ch": dict(OTHER_REF)}):
            for item in out["listings"]:
                if item["region"] != "ch":
                    continue
                expected = round(evd._distance_km(item["lat"], item["lon"], "ch"), 2)
                self.assertEqual(item["distanceKm"], expected, item["id"])

    def test_only_swiss_listings_move_and_the_nl_reference_stays(self) -> None:
        out = _overlay.apply_overlay(PUBLIC, {"ref_ch": OTHER_REF})
        self.assertEqual(out["references"]["ch"], OTHER_REF)
        self.assertEqual(out["references"]["nl"], PUBLIC["references"]["nl"])
        for new, old in zip(out["listings"], PUBLIC["listings"]):
            if old["region"] == "nl":
                self.assertEqual(new["distanceKm"], old["distanceKm"])

    def test_listing_without_coordinates_is_left_alone(self) -> None:
        payload = {"references": {"ch": {"label": "a", "lat": 1, "lon": 1}},
                   "listings": [{"id": "x", "region": "ch", "lat": None, "lon": None, "distanceKm": 7.0}]}
        out = _overlay.apply_overlay(payload, {"ref_ch": OTHER_REF})
        self.assertEqual(out["listings"][0]["distanceKm"], 7.0)


class LimitsTest(unittest.TestCase):
    def test_labels_match_the_exporter(self) -> None:
        for value in (500, 4_500, 5_000, 999_999, 1_000_000, 2_500_000, 4_500_000):
            self.assertEqual(_overlay.chf_limit_label(value), evd._chf_limit_label(value), value)

    def test_limits_replace_the_defaults(self) -> None:
        out = _overlay.apply_overlay(PUBLIC, {"buy_limit_chf": 4_500_000, "rent_limit_chf": 4_500})
        self.assertEqual(out["criteria"]["buy"]["defaultMaxPriceChf"], 4_500_000)
        self.assertEqual(out["criteria"]["buy"]["priceLimitLabel"], "CHF 4.5 Mio.")
        self.assertEqual(out["criteria"]["rent"]["priceLimitLabel"], "CHF 4'500")
        self.assertEqual(out["criteria"]["nl"], PUBLIC["criteria"]["nl"])


class ValidationTest(unittest.TestCase):
    def test_malformed_values_are_ignored_not_raised(self) -> None:
        bad = {
            "ref_ch": {"label": "", "lat": 47.5, "lon": 9.1},
            "buy_limit_chf": True,
            "rent_limit_chf": "5000",
        }
        self.assertEqual(_overlay.apply_overlay(PUBLIC, bad), PUBLIC)

    def test_out_of_range_and_non_finite_numbers_are_rejected(self) -> None:
        for ref in ({"label": "a", "lat": 95, "lon": 9}, {"label": "a", "lat": 47, "lon": 181},
                    {"label": "a", "lat": float("nan"), "lon": 9}, {"label": "a", "lat": 47}):
            self.assertEqual(_overlay.clean_settings({"ref_ch": ref}), {})
        self.assertEqual(_overlay.clean_settings({"buy_limit_chf": float("inf")}), {})
        self.assertEqual(_overlay.clean_settings({"buy_limit_chf": 0}), {})

    def test_label_is_trimmed_and_capped(self) -> None:
        ref = {"label": "  " + "x" * 200 + "  ", "lat": 47.5, "lon": 9.1}
        self.assertEqual(len(_overlay.clean_settings({"ref_ch": ref})["ref_ch"]["label"]), 80)


class FetchSettingsTest(unittest.TestCase):
    def test_no_url_means_no_settings(self) -> None:
        self.assertEqual(_overlay.fetch_settings(None), {})
        self.assertEqual(_overlay.fetch_settings(""), {})

    def test_database_errors_fail_open_without_leaking_the_message(self) -> None:
        secret = "postgresql://user:pw-not-for-logs@host/db"
        with mock.patch("psycopg.connect", side_effect=RuntimeError(secret)), \
                mock.patch("builtins.print") as printed:
            self.assertEqual(_overlay.fetch_settings(secret), {})
        logged = " ".join(str(call.args[0]) for call in printed.call_args_list)
        self.assertIn("RuntimeError", logged)
        self.assertNotIn("pw-not-for-logs", logged)

    def test_row_is_returned_only_if_it_is_a_dict(self) -> None:
        for row, expected in (((OTHER_REF,), OTHER_REF), (("text",), {}), (None, {})):
            conn = mock.MagicMock()
            conn.__enter__.return_value.execute.return_value.fetchone.return_value = row
            with mock.patch("psycopg.connect", return_value=conn):
                self.assertEqual(_overlay.fetch_settings("postgresql://x"), expected)


class HandlerTest(unittest.TestCase):
    def _call(self, env: dict) -> tuple[list, dict]:
        import listings

        h = listings.handler.__new__(listings.handler)
        h.wfile = io.BytesIO()
        sent: list = []
        h.send_response = lambda code: sent.append(("status", code))
        h.send_header = lambda k, v: sent.append((k, v))
        h.end_headers = lambda: None
        with mock.patch.dict("os.environ", env, clear=False):
            h.do_GET()
        return sent, json.loads(h.wfile.getvalue() or b"{}")

    def test_without_database_it_serves_the_public_data_privately_cached(self) -> None:
        sent, body = self._call({"HAUS_SETTINGS_DB_URL": ""})
        self.assertIn(("status", 200), sent)
        self.assertIn(("Cache-Control", "private, no-store"), sent)
        self.assertEqual(body, PUBLIC)

    def test_with_settings_it_serves_the_overlay(self) -> None:
        import listings

        with mock.patch.object(listings, "fetch_settings", return_value={"buy_limit_chf": 2_000_000}):
            sent, body = self._call({"HAUS_SETTINGS_DB_URL": "postgresql://x"})
        self.assertEqual(body["criteria"]["buy"]["priceLimitLabel"], "CHF 2 Mio.")


if __name__ == "__main__":
    unittest.main()

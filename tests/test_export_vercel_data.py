"""Regression tests for the street-coordinate fallback table.

These exist because LOCATION_HINTS coordinates are hand-maintained and have
drifted silently before: "weiherstrasse" was once ~700m from the real street,
in the town centre, which mis-placed every AFORIA listing (AFORIA pages don't
embed their own coordinates, so they always fall back to this table).

Run with: PYTHONPATH=src .venv/bin/python3 -m unittest discover tests
"""

from __future__ import annotations

import math
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import export_vercel_data as evd


# Independently verified via OpenStreetMap/Nominatim (see commit history),
# kept separate from LOCATION_HINTS so this test can't pass by construction
# if someone edits both the source table and this file with the same typo.
VERIFIED_STREET_COORDS = {
    "weiherstrasse": (47.6513, 9.1643),
    "neugasse": (47.6435, 9.1685),
    "holzaeckerlistrasse": (47.6370, 9.1850),
    "gottlieberstrasse": (47.6598, 9.1361),
    "taegerwilen": (47.6564, 9.1311),
}

# Street-level geocoding is inherently approximate; this just needs to catch
# "wrong street / wrong side of town" mistakes like the original bug
# (0.5km-2.5km off), not demand house-number precision.
MAX_DRIFT_KM = 0.3


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_lat = p2 - p1
    d_lon = math.radians(lon2 - lon1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lon / 2) ** 2
    return 2 * radius_km * math.asin(math.sqrt(a))


class LocationHintsTest(unittest.TestCase):
    def test_each_hint_matches_its_verified_coordinate(self) -> None:
        for key, (expected_lat, expected_lon) in VERIFIED_STREET_COORDS.items():
            with self.subTest(street=key):
                lat, lon, _precision, label = evd.LOCATION_HINTS[key]
                drift_km = _haversine_km(lat, lon, expected_lat, expected_lon)
                self.assertLessEqual(
                    drift_km,
                    MAX_DRIFT_KM,
                    f"{label!r} hint drifted {drift_km:.2f}km from its verified "
                    f"coordinate (max allowed {MAX_DRIFT_KM}km) - re-verify against "
                    "Nominatim before changing",
                )

    def test_duplicate_hint_after_normalization_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Duplicate location hint"):
            evd._build_location_hints(
                [
                    ("Weiherstrasse", 47.0, 9.0, "street-guess"),
                    ("weiherstraße", 47.0, 9.0, "street-guess"),
                ]
            )

    def test_coords_for_prefers_address_over_mentioned_landmark(self) -> None:
        # The address is on Neugasse; the description only *mentions*
        # Weiherstrasse as a nearby landmark. The address field must win.
        address = "Neugasse 5, 8280 Kreuzlingen"
        fallback_text = (
            f"{address} Ruhige Lage, 5 Gehminuten von der Weiherstrasse entfernt."
        )

        lat, lon, _precision, label = evd._coords_for(address, fallback_text)

        self.assertEqual(label, "Neugasse")
        self.assertEqual((lat, lon), evd.LOCATION_HINTS["neugasse"][:2])

    def test_coords_for_falls_back_to_full_text_when_address_has_no_hint(self) -> None:
        # Address parsing failed (a common occurrence for noisy scraped
        # pages), but the description names a specific street.
        address = ""
        fallback_text = "Charmantes Haus an der Gottlieberstrasse."

        lat, lon, _precision, label = evd._coords_for(address, fallback_text)

        self.assertEqual(label, "Gottlieberstrasse")
        self.assertEqual((lat, lon), evd.LOCATION_HINTS["gottlieberstrasse"][:2])

    def test_coords_for_returns_generic_fallback_when_nothing_matches(self) -> None:
        lat, lon, precision, label = evd._coords_for(
            "", "Ein Objekt ohne Strassenangabe."
        )

        self.assertEqual(precision, "fallback")
        self.assertEqual(label, "Kreuzlingen")
        self.assertEqual((lat, lon), (47.6458, 9.1804))


def _candidate_row(**overrides: object) -> dict[str, object]:
    """A row that passes _is_candidate unless a test breaks it on purpose."""
    row = {
        "category": "buy",
        "title": "Einfamilienhaus in Kreuzlingen",
        "address": "Musterweg 1, 8280 Kreuzlingen",
        "raw_text": "Grosses Haus in 8280 Kreuzlingen.",
        "url": "https://example-makler.ch/immobilien/1234de-einfamilienhaus/",
        "rooms": 5.5,
        "area_sqm": 140,
        "image_status": "clear",
    }
    row.update(overrides)
    return row


class CandidateUrlGuardsTest(unittest.TestCase):
    def test_baseline_row_is_a_candidate(self) -> None:
        self.assertTrue(evd._is_candidate(_candidate_row()))

    def test_pagination_urls_are_rejected(self) -> None:
        # The Hueppin "IMMOBILIEN KAUFEN" fake row: an overview pagination
        # page recorded as a listing, carrying a sold house's address.
        for url in (
            "https://hueppin-immobilien.ch/immobilien/page/2/?availabilities%5B0%5D=active&availabilities%5B1%5D=taken&availabilities%5B2%5D=reserved&salestypes%5B0%5D=buy",
            "https://hueppin-immobilien.ch/immobilien/page/4/",
            "https://example-makler.ch/immobilien/?pageNum=3",
        ):
            with self.subTest(url=url):
                self.assertFalse(evd._is_candidate(_candidate_row(url=url)))

    def test_manually_delisted_urls_are_rejected(self) -> None:
        # Slug darf kein Statuswort enthalten, sonst greift schon das
        # EXCLUDE_KEYWORDS-Gate statt der Blocklist.
        url = "https://example-makler.ch/immobilien/999de-einfamilienhaus/"
        self.assertTrue(evd._is_candidate(_candidate_row(url=url)))
        with mock.patch.object(evd, "MANUALLY_DELISTED_URLS", {url}):
            self.assertFalse(evd._is_candidate(_candidate_row(url=url)))


class FreshRowsTest(unittest.TestCase):
    """Rows the current run did not refresh are delisted at their source.

    Regression: immoscout24.ch/kaufen/4003261712 vanished from ImmoStreet
    between the 2026-07-14 and 2026-07-15 runs, yet its stale history row
    reached the site on 2026-07-15 once the RealAdvisor twin (which had
    absorbed it in _dedupe) was delisted.
    """

    NOW = datetime(2026, 7, 15, 17, 57, tzinfo=timezone.utc)

    MAX_AGE = evd.MAX_ROW_AGE_HOURS

    def test_rows_from_the_current_run_are_kept(self) -> None:
        row = {"fetched_at_utc": self.NOW - timedelta(minutes=10)}
        self.assertEqual(evd._fresh_rows([row], self.MAX_AGE, now=self.NOW), [row])

    def test_rows_missed_by_the_current_run_are_dropped(self) -> None:
        row = {"fetched_at_utc": self.NOW - timedelta(hours=24, minutes=4)}
        self.assertEqual(evd._fresh_rows([row], self.MAX_AGE, now=self.NOW), [])

    def test_string_timestamps_from_sqlite_are_understood(self) -> None:
        fresh = {"fetched_at_utc": "2026-07-15 17:45:00"}
        stale = {"fetched_at_utc": "2026-07-12 17:49:36.527943+00"}
        self.assertEqual(
            evd._fresh_rows([fresh, stale], self.MAX_AGE, now=self.NOW), [fresh]
        )

    def test_rows_without_timestamp_always_pass(self) -> None:
        # Extra-URL rows are fetched live during the export and carry none.
        row = {"url": "https://realadvisor.ch/de/kaufen/haus/..."}
        self.assertEqual(evd._fresh_rows([row], self.MAX_AGE, now=self.NOW), [row])

    def test_fresh_extra_row_survives_its_stale_history_twin(self) -> None:
        # An extra-URL listing that fell out of every overview: its history
        # row is stale, but the export just fetched it live (no timestamp).
        # Freshness must run BEFORE the per-URL dedupe, otherwise the stale
        # row wins the dedupe and is then dropped — removing exactly the
        # manually pinned listing the mechanism exists to keep.
        url = "https://realadvisor.ch/de/kaufen/haus/8280-kreuzlingen-YA65-GQ4V"
        stale = {"url": url, "fetched_at_utc": self.NOW - timedelta(days=3)}
        extra = {"url": url}
        rows = evd._latest_rows(
            evd._fresh_rows([stale, extra], self.MAX_AGE, now=self.NOW)
        )
        self.assertEqual(rows, [extra])


if __name__ == "__main__":
    unittest.main()

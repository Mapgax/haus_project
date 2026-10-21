"""CI copies config/config.example.yaml to config.yaml and scrapes with it.

A truncated or stubbed example silently turns the daily run into a near-empty
scrape, so pin the shape of the file.
"""

from __future__ import annotations

from pathlib import Path
import unittest
from urllib.parse import urlparse

from hausfinder.config import load_config

EXAMPLE = Path(__file__).resolve().parents[1] / "config" / "config.example.yaml"


class ExampleConfigTest(unittest.TestCase):
    def test_example_config_keeps_its_real_source_list(self) -> None:
        config = load_config(str(EXAMPLE))
        enabled = [s for s in config.sources if s.enabled]
        self.assertGreaterEqual(len(enabled), 20, "example config lost its sources")
        regions = {getattr(s, "region", "ch") for s in enabled}
        self.assertEqual(regions, {"ch", "nl"})
        for source in enabled:
            host = urlparse(source.url_buy or "").hostname or ""
            self.assertNotIn("_", host, f"placeholder host: {source.name}")


if __name__ == "__main__":
    unittest.main()

"""Regression tests for overview-link collection.

The Hueppin overview's "Seite 2" anchor
(/immobilien/page/2/?availabilities[0]=active&availabilities[1]=taken…)
matches the "/immobilien/" listing keyword, so the scraper once fetched it as
a detail page and produced a fake "IMMOBILIEN KAUFEN" listing whose address
came from the first (sold) card on that page.

Run with: PYTHONPATH=src .venv/bin/python3 -m unittest discover tests
"""

from __future__ import annotations

import unittest

from hausfinder.config import SourceConfig
from hausfinder.sources import _collect_listing_links


BASE_URL = "https://hueppin-immobilien.ch/immobilien/?availabilities%5B%5D=active"

OVERVIEW_HTML = """
<html><body>
  <a href="/immobilien/1671998de-luxurioeses-85-zimmer-wohnerlebnis/">Objekt A</a>
  <a href="/immobilien/1686957de-attraktive-55-zimmer-doppelhaushaelfte/">Objekt B</a>
  <a href="/immobilien/page/2/?availabilities%5B0%5D=active&availabilities%5B1%5D=taken&availabilities%5B2%5D=reserved&salestypes%5B0%5D=buy">2</a>
  <a href="/immobilien/page/3/">3</a>
  <a href="/immobilien/?pageNum=4">4</a>
  <a href="https://other-host.example/immobilien/999/">fremder Host</a>
</body></html>
"""


class CollectListingLinksTest(unittest.TestCase):
    def test_detail_links_are_collected(self) -> None:
        source = SourceConfig(name="Hueppin Immobilien")
        links = _collect_listing_links(BASE_URL, OVERVIEW_HTML, source)
        self.assertEqual(
            links,
            [
                "https://hueppin-immobilien.ch/immobilien/1671998de-luxurioeses-85-zimmer-wohnerlebnis/",
                "https://hueppin-immobilien.ch/immobilien/1686957de-attraktive-55-zimmer-doppelhaushaelfte/",
            ],
        )

if __name__ == "__main__":
    unittest.main()

"""Tests for the sold-banner OCR safety net.

find_status_keyword is a pure text matcher (no tesseract needed); the one
end-to-end OCR test generates its own banner image and is skipped where no
tesseract binary is installed (CI installs it via apt).

Run with: PYTHONPATH=src python3 -m unittest discover tests
"""

from __future__ import annotations

import io
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bs4 import BeautifulSoup

from hausfinder.imagecheck import check_title_image, find_status_keyword
from hausfinder.sources import _extract_image_url


class FindStatusKeywordTest(unittest.TestCase):
    def test_uppercase_banner_text(self) -> None:
        self.assertEqual(find_status_keyword("VERKAUFT"), "verkauft")

    def test_keyword_in_ocr_noise(self) -> None:
        self.assertEqual(
            find_status_keyword("q3 xx\nVerkocht\n| zz"), "verkocht"
        )

    def test_multiword_with_collapsed_whitespace(self) -> None:
        self.assertEqual(find_status_keyword("onder   bod"), "onder bod")

    def test_reserviert(self) -> None:
        self.assertEqual(find_status_keyword("Reserviert!"), "reserviert")

    def test_word_boundaries_protect_against_noise(self) -> None:
        # "sold" must not fire inside other words.
        self.assertIsNone(find_status_keyword("household items"))
        self.assertIsNone(find_status_keyword("unsold inventory"))

    def test_empty_input(self) -> None:
        self.assertIsNone(find_status_keyword(""))
        self.assertIsNone(find_status_keyword("   \n  "))


class ExtractImageUrlTest(unittest.TestCase):
    BASE = "https://broker.example/immobilien/objekt-1/"

    def _soup(self, html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "html.parser")

    def test_og_image_preferred(self) -> None:
        soup = self._soup(
            '<meta property="og:image" content="/media/lead.jpg">'
            '<img src="/media/other.jpg">'
        )
        self.assertEqual(
            _extract_image_url(self.BASE, soup),
            "https://broker.example/media/lead.jpg",
        )

    def test_casasoft_fallback_to_first_content_img(self) -> None:
        # Casasoft pages (Hueppin) set no og:image; the sold banner lives
        # only in the gallery image, so the first content <img> must win.
        soup = self._soup(
            '<img src="/assets/logo.svg">'
            '<img src="/theme/icon-search.png">'
            '<img src="/media/gallery/1671998-titel.jpg">'
        )
        self.assertEqual(
            _extract_image_url(self.BASE, soup),
            "https://broker.example/media/gallery/1671998-titel.jpg",
        )

    def test_no_image_at_all(self) -> None:
        self.assertEqual(_extract_image_url(self.BASE, self._soup("<p>x</p>")), "")


class CheckTitleImageErrorTest(unittest.TestCase):
    def test_network_error_fails_soft(self) -> None:
        import requests

        with mock.patch(
            "hausfinder.imagecheck.requests.get",
            side_effect=requests.RequestException("boom"),
        ):
            self.assertEqual(check_title_image("https://x/img.jpg"), ("error", None))

    def test_broken_image_fails_soft(self) -> None:
        response = mock.Mock()
        response.content = b"not an image"
        response.raise_for_status = mock.Mock()
        with mock.patch("hausfinder.imagecheck.requests.get", return_value=response):
            self.assertEqual(check_title_image("https://x/img.jpg"), ("error", None))


@unittest.skipUnless(shutil.which("tesseract"), "tesseract binary not installed")
class CheckTitleImageOcrTest(unittest.TestCase):
    def _banner_image_bytes(self, text: str, angle: int) -> bytes:
        from PIL import Image, ImageDraw

        banner = Image.new("L", (500, 120), color=255)
        draw = ImageDraw.Draw(banner)
        # Default bitmap font is small; scale up for a banner-like look.
        draw.text((20, 40), text, fill=0)
        banner = banner.resize((1000, 240))
        canvas = Image.new("L", (1200, 800), color=200)
        rotated = banner.rotate(angle, expand=True, fillcolor=200)
        canvas.paste(rotated, (100, 200))
        buffer = io.BytesIO()
        canvas.convert("RGB").save(buffer, format="PNG")
        return buffer.getvalue()

    def test_diagonal_verkauft_banner_detected(self) -> None:
        response = mock.Mock()
        response.content = self._banner_image_bytes("VERKAUFT", angle=30)
        response.raise_for_status = mock.Mock()
        with mock.patch("hausfinder.imagecheck.requests.get", return_value=response):
            status, keyword = check_title_image("https://x/banner.png")
        self.assertEqual((status, keyword), ("sold", "verkauft"))

    def test_low_contrast_cream_banner_detected(self) -> None:
        # Synthetic stand-in for a real broker photo: brown VERKAUFT text on
        # a cream box. The pre-2026-07 pipeline (hard 140 threshold +
        # --psm 11 only) returned "clear" on this kind of banner, which kept
        # sold houses on the map; grayscale + --psm 6 reads it.
        fixture = Path(__file__).parent / "fixtures" / "sold_banner_synthetic.jpg"
        response = mock.Mock()
        response.content = fixture.read_bytes()
        response.raise_for_status = mock.Mock()
        with mock.patch("hausfinder.imagecheck.requests.get", return_value=response):
            status, keyword = check_title_image("https://x/banner.jpg")
        self.assertEqual((status, keyword), ("sold", "verkauft"))

    def test_clean_photo_is_clear(self) -> None:
        from PIL import Image

        image = Image.new("RGB", (800, 600), color=(120, 140, 160))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG")
        response = mock.Mock()
        response.content = buffer.getvalue()
        response.raise_for_status = mock.Mock()
        with mock.patch("hausfinder.imagecheck.requests.get", return_value=response):
            status, keyword = check_title_image("https://x/photo.jpg")
        self.assertEqual((status, keyword), ("clear", None))


if __name__ == "__main__":
    unittest.main()

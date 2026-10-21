"""OCR safety net for sold banners burned into listing title images.

Some brokers keep sold objects online with the only "Verkauft" marker being
a banner rendered into the title photo (Casasoft widgets in particular),
where neither page text nor URL carries the status. This module downloads
the title image and looks for status keywords via tesseract.

Deliberately not a trained model: a keyword OCR match is auditable, needs
no training data, and a false positive is cheap because the verdict only
suppresses the export — the listing row stays in the database and is
re-evaluated on every scrape.

Split in two layers so unit tests never need a tesseract binary:
`find_status_keyword` is a pure text matcher; `check_title_image` does the
download + OCR and imports Pillow/pytesseract lazily.
"""

from __future__ import annotations

import io
import re

import requests

# German, Dutch and English status markers. Word-bounded so OCR noise like
# "household" can never hit "sold"; multi-word markers tolerate collapsed or
# repeated whitespace.
STATUS_KEYWORDS = (
    "verkauft",
    "reserviert",
    "vermietet",
    "sold",
    "verkocht",
    "onder bod",
    "verhuurd",
)

# Sold banners are usually printed diagonally across a photo corner;
# tesseract only reads near-horizontal text, so try a handful of rotations.
ROTATIONS = (0, 30, -30, 45, -45)

_KEYWORD_RES = tuple(
    re.compile(r"\b" + r"\s+".join(map(re.escape, kw.split())) + r"\b")
    for kw in STATUS_KEYWORDS
)


def find_status_keyword(ocr_text: str) -> str | None:
    """Return the matched status keyword in the OCR output, or None."""
    if not ocr_text:
        return None
    haystack = " ".join(ocr_text.lower().split())
    for keyword, pattern in zip(STATUS_KEYWORDS, _KEYWORD_RES):
        if pattern.search(haystack):
            return keyword
    return None


def _ocr_attempts(image):
    """Ordered OCR attempts as (variant image, tesseract psm) pairs.

    The 2026-07-16 variant matrix (Hueppin 1671998 vs. two confirmed hits)
    showed that a low-contrast banner — brown VERKAUFT text on a cream box —
    survives neither the hard 140 threshold nor --psm 11 segmentation, while
    plain autocontrasted grayscale with --psm 6 read every real-world banner
    at rotation 0. That attempt therefore comes first (sold images exit after
    one call); the thresholded rotations stay as the net for diagonal
    high-contrast banners.
    """
    from PIL import ImageOps

    gray = ImageOps.autocontrast(image.convert("L"))
    yield gray, 6
    binary = gray.point(lambda x: 255 if x > 140 else 0)
    for angle in ROTATIONS:
        rotated = (
            binary if angle == 0 else binary.rotate(angle, expand=True, fillcolor=255)
        )
        yield rotated, 11


def check_title_image(
    image_url: str,
    *,
    timeout: int = 15,
    user_agent: str = "HausfinderBot/0.1 (+personal property search)",
) -> tuple[str, str | None]:
    """OCR a listing title image for sold banners.

    Returns ("sold", keyword) on a match, ("clear", None) when nothing was
    found, and ("error", None) on any failure — network, image decoding, or
    a missing tesseract binary. Errors fail soft: the caller keeps the
    listing and the store never lets an 'error' overwrite a 'sold' verdict.
    """
    try:
        from PIL import Image
        import pytesseract

        response = requests.get(
            image_url,
            headers={"User-Agent": user_agent},
            timeout=timeout,
        )
        response.raise_for_status()
        image = Image.open(io.BytesIO(response.content))
        image.load()
        # Full-resolution photos slow tesseract down without helping the
        # banner text; banners stay readable well below this cap.
        image.thumbnail((1600, 1600))
        for variant, psm in _ocr_attempts(image):
            text = pytesseract.image_to_string(
                variant, lang="deu+nld", config=f"--psm {psm}"
            )
            keyword = find_status_keyword(text)
            if keyword:
                return "sold", keyword
        return "clear", None
    except Exception:  # noqa: BLE001 — any failure must keep the listing
        return "error", None

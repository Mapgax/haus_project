"""Fail if personal data leaks into a versioned file.

This repository is public. The patterns below are deliberately assembled from
fragments so this file does not trip its own scan.
"""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN: dict[str, re.Pattern[str]] = {
    "school / reference street": re.compile("pesta" + "lozzi", re.IGNORECASE),
    "absolute home path": re.compile(r"/Users/[A-Za-z0-9_.-]+/"),
    "private e-mail address": re.compile(
        r"[\w.+-]+@(?:gmail|googlemail|icloud|outlook|hotmail|yahoo|proton|pm)\.\w+|@me\.com",
        re.IGNORECASE,
    ),
    "owner surname": re.compile("doep" + "fert", re.IGNORECASE),
    "hard-coded finance figure": re.compile(
        r"finance_\w+[\"']?\s*[:=]\s*[\"']?\d{4,}", re.IGNORECASE
    ),
}
SELF = Path(__file__).resolve()
TEXT_SUFFIXES = {
    ".py", ".md", ".yaml", ".yml", ".json", ".geojson", ".js", ".css", ".html",
    ".toml", ".sql", ".sh", ".example", ".txt", ".plist", "",
}


def _tracked_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    return [ROOT / name for name in out.decode().split("\0") if name]


class PublicHygieneTest(unittest.TestCase):
    def test_no_personal_patterns_in_tracked_files(self) -> None:
        files = _tracked_files()
        if not files:
            self.skipTest("not inside a git checkout")
        hits: list[str] = []
        for path in files:
            if path.resolve() == SELF or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for label, pattern in FORBIDDEN.items():
                match = pattern.search(text)
                if match:
                    line = text.count("\n", 0, match.start()) + 1
                    hits.append(f"{path.relative_to(ROOT)}:{line}: {label}")
        self.assertEqual(hits, [], "personal data in versioned files:\n" + "\n".join(hits))


if __name__ == "__main__":
    unittest.main()

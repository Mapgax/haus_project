"""GET /api/listings - the public listings payload plus the private overlay.

The page falls back to the static ``assets/listings.json`` if this endpoint is
missing or fails, so the site keeps working without it.
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _overlay import apply_overlay, fetch_settings, load_static  # noqa: E402

STATIC_FILE = Path(__file__).resolve().parent.parent / "assets" / "listings.json"


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - the Vercel runtime dictates the name
        try:
            payload = load_static(STATIC_FILE)
        except Exception:  # noqa: BLE001
            self.send_response(500)
            self.end_headers()
            return

        merged = apply_overlay(payload, fetch_settings(os.environ.get("HAUS_SETTINGS_DB_URL")))
        body = json.dumps(merged, ensure_ascii=False).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        # Personal values: never let a shared cache keep a copy.
        self.send_header("Cache-Control", "private, no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

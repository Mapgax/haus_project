"""Tests for ntfy notifications about new scrape findings.

The send path is deliberately defensive: notifications are best-effort
(at-most-once, never failing the scrape run) and must survive umlauts —
ntfy's header-based publish API is Latin-1 only, which is why the module
publishes JSON against the server root. These tests pin both properties.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from hausfinder.config import NtfyConfig, load_config
from hausfinder.models import Listing
from hausfinder.notify import SITE_URL, send_new_listing_notifications


def _listing(**overrides) -> Listing:
    values = {
        "source_name": "RealAdvisor",
        "category": "buy",
        "title": "Einfamilienhaus in Kreuzlingen",
        "url": "https://example.com/listing/1",
        "address": "Hauptstrasse 1, 8280 Kreuzlingen",
        "price_chf": 1_250_000.0,
        "area_sqm": 160.0,
        "rooms": 5.5,
    }
    values.update(overrides)
    return Listing(**values)


@mock.patch.dict("os.environ", {"NTFY_TOPIC": "test-topic"})
@mock.patch("hausfinder.notify.requests.post")
class SendNotificationsTest(unittest.TestCase):
    def test_no_new_items_sends_nothing(self, post: mock.Mock) -> None:
        self.assertEqual(send_new_listing_notifications([], NtfyConfig()), 0)
        post.assert_not_called()

    def test_disabled_config_sends_nothing(self, post: mock.Mock) -> None:
        sent = send_new_listing_notifications([_listing()], NtfyConfig(enabled=False))
        self.assertEqual(sent, 0)
        post.assert_not_called()

    def test_missing_topic_sends_nothing(self, post: mock.Mock) -> None:
        with mock.patch.dict("os.environ", {"NTFY_TOPIC": ""}):
            sent = send_new_listing_notifications([_listing()], NtfyConfig())
        self.assertEqual(sent, 0)
        post.assert_not_called()

    def test_dry_run_sends_nothing(self, post: mock.Mock) -> None:
        sent = send_new_listing_notifications([_listing()], NtfyConfig(), dry_run=True)
        self.assertEqual(sent, 0)
        post.assert_not_called()

    def test_dry_run_output_never_contains_the_topic(self, post: mock.Mock) -> None:
        import contextlib
        import io

        buffer = io.StringIO()
        with mock.patch.dict("os.environ", {"NTFY_TOPIC": "secret-topic-123"}):
            with contextlib.redirect_stdout(buffer):
                send_new_listing_notifications([_listing()], NtfyConfig(), dry_run=True)
        self.assertIn("ntfy dry-run", buffer.getvalue())
        self.assertNotIn("secret-topic-123", buffer.getvalue())

    def test_one_message_per_listing(self, post: mock.Mock) -> None:
        items = [_listing(url=f"https://example.com/listing/{i}") for i in range(3)]
        sent = send_new_listing_notifications(items, NtfyConfig())
        self.assertEqual(sent, 3)
        self.assertEqual(post.call_count, 3)
        # JSON publish goes to the server ROOT, not <server>/<topic>.
        args, kwargs = post.call_args_list[0]
        self.assertEqual(args[0], "https://ntfy.sh")
        payload = kwargs["json"]
        self.assertEqual(payload["topic"], "test-topic")
        self.assertIn("Neues Objekt (Kauf)", payload["title"])
        self.assertIn("CHF 1'250'000", payload["title"])
        self.assertEqual(payload["click"], "https://example.com/listing/0")

    def test_umlauts_survive_in_payload(self, post: mock.Mock) -> None:
        item = _listing(title="Haus in Tägerwilen — günstig", address="Örlibergstrasse 3")
        send_new_listing_notifications([item], NtfyConfig())
        payload = post.call_args.kwargs["json"]
        self.assertIn("Haus in Tägerwilen — günstig", payload["message"])
        self.assertIn("Örlibergstrasse 3", payload["message"])

    def test_many_items_collapse_into_one_digest(self, post: mock.Mock) -> None:
        items = [_listing(url=f"https://example.com/listing/{i}") for i in range(9)]
        sent = send_new_listing_notifications(items, NtfyConfig(max_individual=8))
        self.assertEqual(sent, 1)
        self.assertEqual(post.call_count, 1)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["title"], "9 neue Objekte gefunden")
        self.assertEqual(payload.get("click"), SITE_URL or None)

    def test_digest_stays_under_ntfy_byte_limit(self, post: mock.Mock) -> None:
        items = [_listing(title="Grosszügiges Anwesen am Bodensee " * 5) for _ in range(50)]
        send_new_listing_notifications(items, NtfyConfig(max_individual=8))
        payload = post.call_args.kwargs["json"]
        self.assertLess(len(payload["message"].encode("utf-8")), 4096)
        self.assertIn("weitere", payload["message"])

    def test_network_error_does_not_raise(self, post: mock.Mock) -> None:
        post.side_effect = requests.RequestException("boom")
        items = [_listing(), _listing()]
        sent = send_new_listing_notifications(items, NtfyConfig())
        self.assertEqual(sent, 0)
        # After the first failure the rest is skipped (no 8x timeout in CI).
        self.assertEqual(post.call_count, 1)

    def test_nl_listing_gets_prefix_and_eur_price(self, post: mock.Mock) -> None:
        item = _listing(
            title="Vrijstaande woning aan de Overrijn",
            url="https://example.nl/aanbod/1",
            price_chf=895_000.0,
            region="nl",
            currency="EUR",
        )
        send_new_listing_notifications([item], NtfyConfig())
        payload = post.call_args.kwargs["json"]
        self.assertIn("NL Kauf", payload["title"])
        self.assertIn("€ 895.000", payload["title"])
        self.assertNotIn("CHF", payload["title"])

    def test_nl_digest_line_uses_eur(self, post: mock.Mock) -> None:
        items = [
            _listing(
                url=f"https://example.nl/aanbod/{i}",
                price_chf=700_000.0 + i,
                region="nl",
                currency="EUR",
            )
            for i in range(9)
        ]
        send_new_listing_notifications(items, NtfyConfig(max_individual=8))
        payload = post.call_args.kwargs["json"]
        self.assertIn("€ 700.000", payload["message"])


class NtfyConfigLoadingTest(unittest.TestCase):
    _MINIMAL_YAML = (
        'sources:\n  - name: "Quelle"\n    url_buy: "https://example.com"\n'
    )

    def _load(self, text: str):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(text)
            path = Path(f.name)
        try:
            return load_config(path)
        finally:
            path.unlink()

    def test_missing_ntfy_section_uses_defaults(self) -> None:
        config = self._load(self._MINIMAL_YAML)
        self.assertTrue(config.ntfy.enabled)
        self.assertEqual(config.ntfy.server_url, "https://ntfy.sh")
        self.assertEqual(config.ntfy.topic_env, "NTFY_TOPIC")

    def test_bare_ntfy_key_uses_defaults(self) -> None:
        config = self._load(self._MINIMAL_YAML + "\nntfy:\n")
        self.assertTrue(config.ntfy.enabled)
        self.assertEqual(config.ntfy.max_individual, 8)

    def test_ntfy_section_overrides(self) -> None:
        config = self._load(
            self._MINIMAL_YAML + "\nntfy:\n  enabled: false\n  max_individual: 3\n"
        )
        self.assertFalse(config.ntfy.enabled)
        self.assertEqual(config.ntfy.max_individual, 3)


if __name__ == "__main__":
    unittest.main()

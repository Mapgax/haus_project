from __future__ import annotations

import argparse
import os

from .config import load_config
from .dashboard import open_dashboard, write_dashboard_html
from .dedupe import SeenStore
from .filtering import apply_filters
from .sources import fetch_all_sources


def _split_by_category(items: list) -> tuple[list, list]:
    buy = [x for x in items if x.category == "buy"]
    rent = [x for x in items if x.category == "rent"]
    return buy, rent


def main() -> int:
    parser = argparse.ArgumentParser(description="Hausfinder daily search runner.")
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        help="Path to YAML config file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print short summary to stdout, but still write dashboard/history.",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    if os.environ.get("HAUSFINDER_OPEN_BROWSER", "").lower() in {"0", "false", "no"}:
        config.dashboard.open_browser = False

    raw = fetch_all_sources(config)
    filtered = apply_filters(raw, config.filters, config.filters_nl)

    if args.dry_run:
        store = SeenStore(config.db_path)
        try:
            recent = store.load_recent_history(limit=config.dashboard.recent_rows)
        finally:
            store.close()
        buy, rent = _split_by_category(filtered)
        print(f"Dry-run: buy={len(buy)} rent={len(rent)} filtered={len(filtered)}")
        dashboard_items = filtered
        table_title = "Passende Treffer (dry-run, ungefiltert nach Duplikaten)"
    else:
        store = SeenStore(config.db_path)
        try:
            store.record_history(filtered)
            new_only = store.filter_new(filtered)
            recent = store.load_recent_history(limit=config.dashboard.recent_rows)
        finally:
            store.close()
        dashboard_items = new_only
        table_title = "Neue Treffer (letzter Lauf)"

    if not config.dashboard.enabled:
        return 0

    dashboard_path = write_dashboard_html(
        output_path=config.dashboard.output_html_path,
        new_items=dashboard_items,
        recent_history=recent,
        table_title=table_title,
    )
    if config.dashboard.open_browser:
        open_dashboard(dashboard_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

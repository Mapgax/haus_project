"""Store the personal values that api/listings.py overlays on the public data.

Usage (values never go into the repo; the owner DB URL comes from .env):

    set -a && source .env && set +a
    python3 scripts/set_private_settings.py \\
        --ref-ch-label "Mein Referenzpunkt" --ref-ch-lat 47.50 --ref-ch-lon 9.10 \\
        --buy-limit-chf 2500000 --rent-limit-chf 4000
    python3 scripts/set_private_settings.py --show          # which keys are set
    python3 scripts/set_private_settings.py --show --reveal # ...and their values
    python3 scripts/set_private_settings.py --clear         # back to public defaults

Only the keys you pass are changed; the rest of the stored row is kept.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
from _overlay import clean_settings  # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS public.haus_private_settings (
    id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.haus_private_settings ENABLE ROW LEVEL SECURITY;
"""


def build_update(args: argparse.Namespace) -> dict:
    update: dict = {}
    ref_args = (args.ref_ch_label, args.ref_ch_lat, args.ref_ch_lon)
    if any(value is not None for value in ref_args):
        if any(value is None for value in ref_args):
            sys.exit("--ref-ch-label, --ref-ch-lat and --ref-ch-lon must be given together")
        update["ref_ch"] = {"label": args.ref_ch_label, "lat": args.ref_ch_lat, "lon": args.ref_ch_lon}
    if args.buy_limit_chf is not None:
        update["buy_limit_chf"] = args.buy_limit_chf
    if args.rent_limit_chf is not None:
        update["rent_limit_chf"] = args.rent_limit_chf

    cleaned = clean_settings(update)
    if set(cleaned) != set(update):
        rejected = ", ".join(sorted(set(update) - set(cleaned)))
        sys.exit(f"value out of range or malformed: {rejected}")
    return cleaned


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ref-ch-label")
    parser.add_argument("--ref-ch-lat", type=float)
    parser.add_argument("--ref-ch-lon", type=float)
    parser.add_argument("--buy-limit-chf", type=float)
    parser.add_argument("--rent-limit-chf", type=float)
    parser.add_argument("--show", action="store_true", help="list the stored keys")
    parser.add_argument("--reveal", action="store_true", help="with --show: print the values too")
    parser.add_argument("--clear", action="store_true", help="delete all stored values")
    args = parser.parse_args()

    db_url = os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        sys.exit("SUPABASE_DB_URL is not set (source .env first)")

    update = {} if (args.show or args.clear) else build_update(args)
    with psycopg.connect(db_url, connect_timeout=10, autocommit=True) as conn:
        conn.execute(DDL)
        if args.clear:
            conn.execute(
                "INSERT INTO public.haus_private_settings (id, settings) VALUES (1, '{}'::jsonb) "
                "ON CONFLICT (id) DO UPDATE SET settings = '{}'::jsonb, updated_at = now()"
            )
            print("cleared: the page shows the public defaults again")
        elif update:
            conn.execute(
                "INSERT INTO public.haus_private_settings (id, settings) VALUES (1, %s::jsonb) "
                "ON CONFLICT (id) DO UPDATE SET settings = public.haus_private_settings.settings || EXCLUDED.settings, "
                "updated_at = now()",
                (json.dumps(update),),
            )
            print("stored keys:", ", ".join(sorted(update)))
        row = conn.execute("SELECT settings, updated_at FROM public.haus_private_settings WHERE id = 1").fetchone()

    stored = row[0] if row else {}
    if args.show or update:
        print("keys now set:", ", ".join(sorted(stored)) or "(none)", "| updated:", row[1] if row else "-")
        if args.reveal:
            print(json.dumps(stored, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

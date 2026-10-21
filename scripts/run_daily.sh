#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -d .venv ]]; then
  echo "Missing .venv. Run setup first." >&2
  exit 1
fi

git pull --ff-only origin main

HAUSFINDER_OPEN_BROWSER=0 PYTHONPATH=src .venv/bin/python3 -m hausfinder.main --config config/config.yaml
PYTHONPATH=src .venv/bin/python3 scripts/export_vercel_data.py --source live --output assets/listings.json

if git diff --quiet -- assets/listings.json; then
  echo "No Vercel listing changes to publish."
  exit 0
fi

git add assets/listings.json
git commit -m "Update property listings"
git push origin main

#!/usr/bin/env bash
# Render JavaScript-only / blocked pages in headless Chromium on this Mac. Keeps the Mac awake; safe to rerun.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/pip install -q -r scripts/requirements-pages.txt playwright
.venv/bin/python -m playwright install chromium
cd scripts
exec caffeinate -i ../.venv/bin/python render_pages.py "$@"

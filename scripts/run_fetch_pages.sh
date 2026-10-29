#!/usr/bin/env bash
# Fetch the non-GitHub pages on this Mac. Keeps the Mac awake while running; safe to stop and rerun.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip >/dev/null
.venv/bin/pip install -q -r scripts/requirements-pages.txt
cd scripts
exec caffeinate -i ../.venv/bin/python fetch_pages.py "$@"

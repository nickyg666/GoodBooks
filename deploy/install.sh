#!/usr/bin/env bash
# GoodBooks installer. Idempotent: safe to run again on a configured host.
#
# Installs: python deps, the systemd unit, the runtime directories.
# Never touches: the book library, data/settings.json, or any download
# history. Those are user data, not part of the installation.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_SRC="$ROOT/deploy/GoodBooks.service"
UNIT_DST="/etc/systemd/system/GoodBooks.service"
SERVICE_USER="${GOODBOOKS_USER:-$(id -un)}"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

say "GoodBooks installer"
echo "    root      : $ROOT"
echo "    user      : $SERVICE_USER"

# ---- 1. refuse if python is missing -------------------------------------
command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }
PYV=$(python3 -V 2>&1)
echo "    python    : $PYV"

# ---- 2. dependencies -----------------------------------------------------
# Prefer a venv; fall back to the system interpreter. PEP 668 makes a bare
# "pip install" fail on Debian/Ubuntu, which is where this runs.
say "installing python dependencies"
REQ="$ROOT/requirements.txt"
if [ -f "$REQ" ]; then
  if python3 -m venv --help >/dev/null 2>&1; then
    if [ ! -d "$ROOT/.venv" ]; then
      python3 -m venv "$ROOT/.venv"
    fi
    "$ROOT/.venv/bin/pip" install --quiet --upgrade pip
    "$ROOT/.venv/bin/pip" install --quiet -r "$REQ"
    echo "    installed into $ROOT/.venv"
    PY="$ROOT/.venv/bin/python3"
  else
    python3 -m pip install --quiet --break-system-packages -r "$REQ" \
      || { echo "dependency install failed"; exit 1; }
    PY="python3"
  fi
else
  PY="python3"
fi

# ---- 3. runtime directories ---------------------------------------------
say "ensuring runtime directories"
mkdir -p "$ROOT/data" "$ROOT/data/covers" "$ROOT/data/temp" \
         "$ROOT/data/uploads" "$ROOT/logs"
echo "    data/temp holds conversions; safe to clear when stopped"

# ---- 4. the systemd unit -------------------------------------------------
say "installing systemd unit"
[ -f "$UNIT_SRC" ] || { echo "missing $UNIT_SRC"; exit 1; }
# Substitute the running user so a checkout by another user still works.
sed "s/^User=.*/User=$SERVICE_USER/; s/^Group=.*/Group=$SERVICE_USER/" \
    "$UNIT_SRC" > "$UNIT_DST"
echo "    wrote $UNIT_DST"

if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload
  echo "    daemon-reloaded"
  echo
  echo "    start with:  systemctl enable --now GoodBooks.service"
  echo "    check with:  systemctl status GoodBooks.service"
else
  echo "    systemctl not available; start manually:"
  echo "      cd $ROOT && xvfb-run -a $PY app.py"
fi

# ---- 5. playwright browser ---------------------------------------------
# stealth_browser drives a real Chromium for Anna's Archive. Without it the
# download path silently degrades, so say so rather than leaving a mystery.
say "checking browser for the stealth fetcher"
if "$PY" -c "import playwright" 2>/dev/null; then
  if "$PY" -m playwright install --dry-run chromium >/dev/null 2>&1; then
    :
  fi
  BROWSERS="$("$PY" -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    print(p.chromium.executable_path)
" 2>/dev/null || true)"
  if [ -n "$BROWSERS" ] && [ -x "$BROWSERS" ]; then
    echo "    chromium present"
  else
    echo "    chromium NOT installed; run:"
    echo "      $PY -m playwright install chromium"
  fi
else
  echo "    playwright not importable; downloads will use HTTP only"
fi

say "done"
echo "Your library under the configured library_root is untouched."
echo "Start the service, then open the port from settings.json (default 5000)."

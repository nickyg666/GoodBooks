#!/usr/bin/env bash
# Prove the installation works. Exit non-zero on any failure.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY="python3"
fail=0
ok()   { printf '  \033[32mok\033[0m   %s\n' "$*"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$*"; fail=1; }

echo "== python =="
"$PY" -c 'import sys; print("   ", sys.version.split()[0])' || bad "python unusable"
for m in flask feedparser bs4 requests lxml PIL pytz; do
  "$PY" -c "import $m" 2>/dev/null && ok "import $m" || bad "import $m"
done

echo "== application =="
"$PY" -m py_compile app.py search_engine.py && ok "app.py + search_engine.py compile" \
  || bad "compile failed"
"$PY" -c "
import app
routes = sorted({r.rule for r in app.app.url_map.iter_rules()})
assert len(routes) > 25, len(routes)
print('    routes:', len(routes))
" >/dev/null 2>&1 && ok "app imports and registers routes" \
  || bad "app failed to import"

echo "== data files =="
[ -f data/library_metadata.json ] && \
  "$PY" -c "import json;d=json.load(open('data/library_metadata.json'));print('    metadata entries:',len(d))" \
  || bad "library_metadata.json missing or unreadable"
[ -f data/settings.json ] && ok "settings.json present" || bad "settings.json missing"

echo "== service =="
if systemctl list-unit-files 2>/dev/null | grep -q GoodBooks; then
  ok "unit installed"
  systemctl is-active --quiet GoodBooks && ok "service active" \
    || echo "  note service not active (fine if you have not started it yet)"
else
  echo "  note unit not installed; run deploy/install.sh"
fi

echo
[ $fail -eq 0 ] && echo "ALL CHECKS PASSED" || echo "SOME CHECKS FAILED"
exit $fail

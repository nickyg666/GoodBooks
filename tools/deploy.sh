#!/usr/bin/env bash
# ============================================================================
# GoodBooks deploy script
#
# Workflow:
#   1. Snapshot the live service (settings, library metadata)
#   2. Run the test suite
#   3. If tests pass, restart the systemd service
#   4. Smoke-test the new instance
#   5. If anything fails, roll back
#
# Usage:
#   tools/deploy.sh                # deploy current working tree
#   tools/deploy.sh --skip-tests   # skip tests (not recommended)
#   tools/deploy.sh --no-restart   # don't restart the service
#   tools/deploy.sh --rollback     # roll back to the previous deploy
# ============================================================================
set -euo pipefail

SERVICE="${SERVICE:-GoodBooks}"
BASE_URL="${BASE_URL:-http://127.0.0.1:5000}"
PYTEST="${PYTEST:-pytest}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="$ROOT/.deploy-backups"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
SNAPSHOT="$BACKUP_DIR/$TIMESTAMP"

SKIP_TESTS=0
NO_RESTART=0
ROLLBACK=0

for arg in "$@"; do
    case "$arg" in
        --skip-tests) SKIP_TESTS=1 ;;
        --no-restart) NO_RESTART=1 ;;
        --rollback) ROLLBACK=1 ;;
        -h|--help)
            grep -E '^#( |$)' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "unknown arg: $arg" >&2; exit 2 ;;
    esac
done

mkdir -p "$BACKUP_DIR"

snapshot() {
    echo ">> Snapshotting pre-deploy state to $SNAPSHOT"
    mkdir -p "$SNAPSHOT"
    cp -a "$ROOT/data/settings.json" "$SNAPSHOT/settings.json" 2>/dev/null || true
    cp -a "$ROOT/data/library_metadata.json" "$SNAPSHOT/library_metadata.json" 2>/dev/null || true
}

rollback() {
    local target
    target="$(ls -1 "$BACKUP_DIR" | grep -v current | sort | tail -1 || true)"
    if [ -z "$target" ]; then
        echo "!! No previous snapshot to roll back to" >&2
        exit 1
    fi
    echo ">> Rolling back to $target"
    cp -a "$BACKUP_DIR/$target/settings.json" "$ROOT/data/" 2>/dev/null || true
    cp -a "$BACKUP_DIR/$target/library_metadata.json" "$ROOT/data/" 2>/dev/null || true
    if [ "$NO_RESTART" -eq 0 ]; then
        sleep 2 && sudo systemctl restart "$SERVICE"
        sleep 3
        smoke
    fi
    echo ">> Rollback complete"
    exit 0
}

smoke() {
    echo ">> Smoke-testing $BASE_URL"
    local rc=0
    for path in / /api/users /settings /history; do
        code="$(curl -s -o /dev/null -w '%{http_code}' "$BASE_URL$path" || echo 000)"
        if [ "$code" -ge 500 ] || [ "$code" -eq 000 ]; then
            echo "   FAIL: $path -> $code"; rc=1
        else
            echo "   ok:   $path -> $code"
        fi
    done
    return $rc
}

run_tests() {
    echo ">> Running pytest"
    cd "$ROOT"
    $PYTEST "$ROOT/tests" -m "not slow" --base-url "$BASE_URL" "$@"
}

restart_service() {
    echo ">> Restarting $SERVICE"
    sleep 2 && sudo systemctl restart "$SERVICE"
    sleep 3
    if ! sudo systemctl is-active --quiet "$SERVICE"; then
        echo "!! $SERVICE did not come back up" >&2
        sudo journalctl -u "$SERVICE" --no-pager -n 30 >&2
        return 1
    fi
}

if [ "$ROLLBACK" -eq 1 ]; then
    rollback
fi

snapshot

if [ "$SKIP_TESTS" -eq 0 ]; then
    if ! run_tests; then
        echo "!! Tests failed; not restarting" >&2
        exit 1
    fi
fi

if [ "$NO_RESTART" -eq 0 ]; then
    restart_service
    if ! smoke; then
        echo "!! Smoke failed; rolling back" >&2
        rollback
    fi
fi

echo ">> Deploy complete: $TIMESTAMP"
ln -sfn "$TIMESTAMP" "$BACKUP_DIR/current"
echo "$TIMESTAMP" > "$BACKUP_DIR/last"

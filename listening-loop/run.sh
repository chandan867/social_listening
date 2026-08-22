#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# ponytail: one script, no orchestrator
echo "[run] $(date -u +%Y-%m-%dT%H:%M:%SZ) — cycle start" >&2
python3 "$HERE/digest.py" "$@" || { echo "[run] digest failed" >&2; exit 0; }
python3 "$HERE/telegram.py" || { echo "[run] telegram skipped/failed (non-fatal)" >&2; }
echo "[run] cycle done — $(cat "$HERE/data/digest.md" 2>/dev/null | head -n 3)" >&2

#!/bin/sh
# ============================================================
# AnCLI Module Build Script
# Delegates to scripts/build_module.py so source sync, generated
# profile/catalog artifacts, and WebUI profile injection stay canonical.
# ============================================================
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if command -v python3 >/dev/null 2>&1; then
    exec python3 "$SCRIPT_DIR/scripts/build_module.py" "$@"
elif command -v python >/dev/null 2>&1; then
    exec python "$SCRIPT_DIR/scripts/build_module.py" "$@"
else
    echo "[X] python3 or python is required to run scripts/build_module.py" >&2
    exit 1
fi

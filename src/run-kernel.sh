#!/usr/bin/env bash
# Start mustang kernel in dev mode (auto-reload).
set -euo pipefail

cd "$(dirname "$0")/kernel"

exec uv run python -m kernel --port 8200 --dev

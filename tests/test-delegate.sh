#!/usr/bin/env bash
# Run task lifecycle, catalog, usage and shell-wrapper regressions.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
exec "${DELEGATE_PYTHON:-python}" -m unittest discover -s "$REPO/tests" -p 'test_*.py' -v

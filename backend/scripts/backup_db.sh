#!/usr/bin/env bash
# Thin wrapper kept for muscle memory. The real work (and the only
# cross-platform path) is the Python CLI, which uses the SQLite backup API.
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../.."
exec python -m backend.cli backup "$@"

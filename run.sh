#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
source .venv/bin/activate
export IMAGE_LIBRARIAN_CONFIG="${IMAGE_LIBRARIAN_CONFIG:-$PWD/config.yaml}"
exec uvicorn app.main:app --host "${IMAGE_LIBRARIAN_HOST:-127.0.0.1}" --port "${IMAGE_LIBRARIAN_PORT:-8765}"

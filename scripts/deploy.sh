#!/usr/bin/env bash
#
# Build the frontend on the Mac and copy it to the Pi.
#
# The Pi never builds React. It has neither the memory nor the patience, and the
# decision to keep it out shapes the project layout: frontend/ is a standalone
# Vite project, and what lands on the Pi is a directory of static files that
# FastAPI serves.
#
#   ./scripts/deploy.sh [user@host]
#
# Run scripts/install.sh on the Pi first. This only ships new code; it does not
# set anything up.
#
set -euo pipefail

TARGET="${1:-liam@hydrosnooze.local}"
REMOTE_DIR="${REMOTE_DIR:-/opt/hydrosnooze}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Building frontend"
(cd "$ROOT/frontend" && npm ci && npm run build)

echo "Copying static files to $TARGET:$REMOTE_DIR/static"
rsync -avz --delete "$ROOT/frontend/dist/" "$TARGET:$REMOTE_DIR/static/"

echo "Copying backend to $TARGET:$REMOTE_DIR"
# egg-info is left alone deliberately. install.sh installs the backend as an
# editable package, and deleting that directory from under it breaks the import
# on the next restart.
rsync -avz --delete \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  --exclude '.venv' \
  --exclude '*.db' \
  --exclude '*.egg-info' \
  --exclude 'data' \
  "$ROOT/backend/" "$TARGET:$REMOTE_DIR/backend/"

echo "Restarting the service"
# -t so sudo has a terminal to prompt on. Raspberry Pi OS gives the user it
# creates passwordless sudo, so it usually will not ask, but without a terminal
# the failure is "no tty present" rather than a password prompt.
ssh -t "$TARGET" 'sudo systemctl restart hydrosnooze && sleep 2 && systemctl is-active hydrosnooze'

echo "Done. Open http://${TARGET#*@}:8000 on the phone."

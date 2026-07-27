#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${IMAGE_LIBRARIAN_DIR:-$HOME/image_librarian}"
REPO_URL="${IMAGE_LIBRARIAN_REPO:-https://github.com/mathisono/image-classification-bot1.git}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
OPENCLAW_WORKSPACE="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace-image-librarian}"
AGENT_NAME="image_librarian"

if ! command -v git >/dev/null 2>&1; then
  echo "ERROR: git is required. Install git first." >&2
  exit 1
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "ERROR: python3 is required. Install python3 and python3-venv first." >&2
  exit 1
fi

if ! command -v mount.cifs >/dev/null 2>&1; then
  cat <<'WARN'
WARNING: mount.cifs was not found.
Install cifs-utils before using a Windows/SMB image archive:
  sudo apt install -y cifs-utils
WARN
fi

if [ -d "$APP_DIR/.git" ]; then
  echo "Updating existing Image Librarian checkout at $APP_DIR"
  git -C "$APP_DIR" pull --ff-only
else
  echo "Cloning Image Librarian into $APP_DIR"
  git clone "$REPO_URL" "$APP_DIR"
fi

cd "$APP_DIR"
"$PYTHON_BIN" -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

mkdir -p data/run data/logs cache/thumbnails cache/analysis "$OPENCLAW_WORKSPACE"
chmod +x run.sh control.sh mount_smb_share.sh 2>/dev/null || true
cp OPENCLAW_IMAGE_LIBRARIAN_PROMPT.md "$OPENCLAW_WORKSPACE/IMAGE_LIBRARIAN_PROMPT.md"
cat > "$OPENCLAW_WORKSPACE/IDENTITY.md" <<'MD'
# Identity: Image Librarian

Name: Image Librarian
Emoji: 🖼️
Theme: local-first private photo archive assistant

The realtime_mini_voice agent coordinates the service. Betty performs local image-to-text classification through GLM-4.6V-Flash. The browser dashboard is the operational system of record.
MD

cat <<EOF
Installed Image Librarian at: $APP_DIR
OpenClaw workspace prompt: $OPENCLAW_WORKSPACE/IMAGE_LIBRARIAN_PROMPT.md

Next steps:
1) Mount the Windows share read-only:
   $APP_DIR/mount_smb_share.sh //SERVER/Share /mnt/image-archive
2) Add /mnt/image-archive to config.yaml with shared: true.
3) Verify access:
   $APP_DIR/control.sh check-share
4) Start the web UI and Betty worker pool from OpenClaw:
   $APP_DIR/control.sh start
5) Open: http://127.0.0.1:8765

Service commands:
   $APP_DIR/control.sh start
   $APP_DIR/control.sh status
   $APP_DIR/control.sh stop
   $APP_DIR/control.sh restart
   $APP_DIR/control.sh logs

Optional OpenClaw agent registration:
   openclaw agents add $AGENT_NAME --workspace $OPENCLAW_WORKSPACE --non-interactive
   openclaw agents set-identity --workspace $OPENCLAW_WORKSPACE --from-identity

Safety: original image files are not deleted or modified.
EOF

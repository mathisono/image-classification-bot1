#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${IMAGE_LIBRARIAN_DIR:-$HOME/image_librarian}"
REPO_URL="${IMAGE_LIBRARIAN_REPO:-https://github.com/mathisono/image-classification-bot1.git}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
OPENCLAW_WORKSPACE="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace-image-librarian}"

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
# Identity: Image Librarian Control

Coordinator: realtime_mini_voice
Vision agent: Betty
Vision model: lmstudio/zai-org/glm-4.6v-flash
Interface: local browser dashboard

The realtime_mini_voice agent controls the service. Betty workers perform local image-to-text classification. The browser dashboard is the operational system of record.
MD

cat <<EOF
Installed Image Librarian at: $APP_DIR
OpenClaw control prompt: $OPENCLAW_WORKSPACE/IMAGE_LIBRARIAN_PROMPT.md

Next steps:
1) Create a private SMB credentials file as described in:
   $APP_DIR/WINDOWS_SHARE_SETUP.md
2) Mount the Windows share read/write:
   SMB_CREDENTIALS_FILE="$HOME/.smbcredentials/image-librarian.cred" \
   SMB_READ_ONLY=false \
   $APP_DIR/mount_smb_share.sh //SERVER/Share /mnt/image-archive
3) Edit $APP_DIR/config.yaml:
   - enable the Windows image root
   - set database_sync.enabled: true
   - set database_sync.share_copy under /mnt/image-archive/.image_librarian/
4) Verify access:
   $APP_DIR/control.sh check-share
5) Start the web UI, Betty worker pool, and database synchronization:
   $APP_DIR/control.sh start
6) Open: http://127.0.0.1:8765

Service commands:
   $APP_DIR/control.sh start
   $APP_DIR/control.sh status
   $APP_DIR/control.sh sync-now
   $APP_DIR/control.sh stop
   $APP_DIR/control.sh restart
   $APP_DIR/control.sh logs

OpenClaw integration:
- Keep realtime_mini_voice as the existing main coordinator.
- Add the instructions from $OPENCLAW_WORKSPACE/IMAGE_LIBRARIAN_PROMPT.md to that agent's workspace or operating instructions.
- Do not create a second image_librarian coordinator unless you intentionally want a separate control agent.

Safety: original image files are not deleted, moved, renamed, or overwritten.
EOF

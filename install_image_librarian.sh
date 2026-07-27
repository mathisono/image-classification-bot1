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
Install cifs-utils before using the web SMB setup:
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

First-run setup:
1) Start only the local configuration UI:
   $APP_DIR/control.sh setup
2) Open:
   http://127.0.0.1:8765/setup
3) In the web page:
   - enter the Windows SMB share and credentials, or browse an existing mount
   - select the exact folder to classify
   - verify the index destination inside that selected folder
   - review and merge any nested Image Librarian indexes
4) Start the complete service after setup:
   $APP_DIR/control.sh restart
5) Verify:
   $APP_DIR/control.sh check-share
   $APP_DIR/control.sh status

Service commands:
   $APP_DIR/control.sh setup
   $APP_DIR/control.sh start
   $APP_DIR/control.sh status
   $APP_DIR/control.sh sync-now
   $APP_DIR/control.sh stop
   $APP_DIR/control.sh restart
   $APP_DIR/control.sh logs

Web SMB mounting uses sudo noninteractively. If the page reports that sudo is unavailable, run mount_smb_share.sh once in a terminal or configure narrowly scoped passwordless sudo for the required mkdir and mount.cifs commands.

OpenClaw integration:
- Keep realtime_mini_voice as the existing main coordinator.
- Add the instructions from $OPENCLAW_WORKSPACE/IMAGE_LIBRARIAN_PROMPT.md to that agent's workspace or operating instructions.
- Do not create a second image_librarian coordinator unless you intentionally want a separate control agent.

Safety: original image files are not deleted, moved, renamed, or overwritten.
EOF

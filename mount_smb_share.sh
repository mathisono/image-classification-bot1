#!/usr/bin/env bash
set -euo pipefail

SHARE="${1:-${IMAGE_LIBRARIAN_SMB_SHARE:-}}"
MOUNT_POINT="${2:-${IMAGE_LIBRARIAN_SMB_MOUNT:-$HOME/image_librarian_smb}}"
USERNAME="${SMB_USERNAME:-}"
DOMAIN="${SMB_DOMAIN:-WORKGROUP}"
CREDENTIALS_FILE="${SMB_CREDENTIALS_FILE:-}"
READ_ONLY="${SMB_READ_ONLY:-true}"
SMB_VERSION="${SMB_VERSION:-3.0}"

if [ -z "$SHARE" ]; then
  cat <<'EOF'
Usage:
  ./mount_smb_share.sh //SERVER/Share /mnt/image-archive

Recommended environment:
  SMB_CREDENTIALS_FILE="$HOME/.smbcredentials/image-librarian.cred"
  SMB_READ_ONLY=true

Credentials file contents:
  username=windows-user
  password=windows-password
  domain=WORKGROUP

Alternative interactive username mode:
  SMB_USERNAME='windows-user' ./mount_smb_share.sh //SERVER/Share /mnt/image-archive
EOF
  exit 1
fi

if ! command -v mount.cifs >/dev/null 2>&1; then
  echo "ERROR: mount.cifs not found. Install cifs-utils first:" >&2
  echo "  sudo apt install -y cifs-utils" >&2
  exit 1
fi

sudo mkdir -p "$MOUNT_POINT"

if mountpoint -q "$MOUNT_POINT"; then
  echo "Already mounted: $MOUNT_POINT"
  exit 0
fi

MODE="rw"
FILE_MODE="0644"
DIR_MODE="0755"
if [ "$READ_ONLY" = "true" ] || [ "$READ_ONLY" = "1" ]; then
  MODE="ro"
  FILE_MODE="0444"
  DIR_MODE="0555"
fi

OPTS="$MODE,iocharset=utf8,vers=$SMB_VERSION,uid=$(id -u),gid=$(id -g),file_mode=$FILE_MODE,dir_mode=$DIR_MODE,noserverino"

if [ -n "$CREDENTIALS_FILE" ]; then
  if [ ! -f "$CREDENTIALS_FILE" ]; then
    echo "ERROR: credentials file not found: $CREDENTIALS_FILE" >&2
    exit 1
  fi
  chmod 600 "$CREDENTIALS_FILE"
  OPTS="$OPTS,credentials=$CREDENTIALS_FILE"
elif [ -n "$USERNAME" ]; then
  OPTS="$OPTS,username=$USERNAME,domain=$DOMAIN"
else
  OPTS="$OPTS,guest"
fi

echo "Mounting $SHARE at $MOUNT_POINT ($MODE)"
sudo mount -t cifs "$SHARE" "$MOUNT_POINT" -o "$OPTS"

if ! mountpoint -q "$MOUNT_POINT"; then
  echo "ERROR: mount did not become active: $MOUNT_POINT" >&2
  exit 1
fi

if ! find "$MOUNT_POINT" -mindepth 1 -maxdepth 1 -print -quit >/dev/null 2>&1; then
  echo "ERROR: mounted share cannot be listed: $MOUNT_POINT" >&2
  exit 1
fi

cat <<EOF
Mounted successfully: $SHARE -> $MOUNT_POINT
Mode: $MODE

Add this root to config.yaml:

image_roots:
  - name: "Windows Image Archive"
    path: "$MOUNT_POINT"
    shared: true
    follow_symlinks: false
    enabled: true

Then verify and start:
  ./control.sh check-share
  ./control.sh start
EOF

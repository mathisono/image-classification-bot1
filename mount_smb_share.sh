#!/usr/bin/env bash
set -euo pipefail

SHARE="${1:-${IMAGE_LIBRARIAN_SMB_SHARE:-}}"
MOUNT_POINT="${2:-${IMAGE_LIBRARIAN_SMB_MOUNT:-$HOME/image_librarian_smb}}"
USERNAME="${SMB_USERNAME:-}"
DOMAIN="${SMB_DOMAIN:-WORKGROUP}"
CREDENTIALS_FILE="${SMB_CREDENTIALS_FILE:-}"
READ_ONLY="${SMB_READ_ONLY:-false}"
SMB_VERSION="${SMB_VERSION:-3.0}"
NONINTERACTIVE="${SMB_SUDO_NONINTERACTIVE:-false}"

if [ -z "$SHARE" ]; then
  cat <<'EOF'
Usage:
  ./mount_smb_share.sh //SERVER/Share /mnt/image-archive

Recommended environment:
  SMB_CREDENTIALS_FILE="$HOME/.smbcredentials/image-librarian.cred"
  SMB_READ_ONLY=false

The share must be writable so the application can store synchronized database
snapshots under .image_librarian/. Original image files remain read-only by
application policy.
EOF
  exit 1
fi

if ! command -v mount.cifs >/dev/null 2>&1; then
  echo "ERROR: mount.cifs not found. Install cifs-utils first:" >&2
  echo "  sudo apt install -y cifs-utils" >&2
  exit 1
fi

SUDO=(sudo)
if [ "$NONINTERACTIVE" = "true" ] || [ "$NONINTERACTIVE" = "1" ]; then
  SUDO=(sudo -n)
fi

if ! "${SUDO[@]}" mkdir -p "$MOUNT_POINT"; then
  echo "ERROR: unable to create mount point noninteractively: $MOUNT_POINT" >&2
  echo "Run the mount helper once in a terminal, or grant narrowly scoped sudo permission for mkdir and mount.cifs." >&2
  exit 1
fi

if mountpoint -q "$MOUNT_POINT"; then
  echo "Already mounted: $MOUNT_POINT"
else
  MODE="rw"
  FILE_MODE="0664"
  DIR_MODE="0775"
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
  if ! "${SUDO[@]}" mount -t cifs "$SHARE" "$MOUNT_POINT" -o "$OPTS"; then
    echo "ERROR: SMB mount failed. Run this helper in a terminal or configure narrowly scoped passwordless sudo for mount.cifs." >&2
    exit 1
  fi
fi

if ! mountpoint -q "$MOUNT_POINT"; then
  echo "ERROR: mount did not become active: $MOUNT_POINT" >&2
  exit 1
fi

if ! find "$MOUNT_POINT" -mindepth 1 -maxdepth 1 -print -quit >/dev/null 2>&1; then
  echo "ERROR: mounted share cannot be listed: $MOUNT_POINT" >&2
  exit 1
fi

if [ "${MODE:-rw}" = "rw" ]; then
  mkdir -p "$MOUNT_POINT/.image_librarian"
  test_file="$MOUNT_POINT/.image_librarian/.write-test-$$"
  printf 'write test\n' > "$test_file"
  rm -f "$test_file"
fi

cat <<EOF
Mounted successfully: $SHARE -> $MOUNT_POINT
Mode: ${MODE:-existing mount}

Open the setup page:
  http://127.0.0.1:8765/setup
EOF

#!/usr/bin/env bash
set -euo pipefail

# Helper for mounting a Windows/SMB share for Image Librarian.
# This does not store credentials by default. For persistent mounts, see README.md.

SHARE="${1:-}"
MOUNT_POINT="${2:-$HOME/image_librarian_smb}"
USERNAME="${SMB_USERNAME:-}"
DOMAIN="${SMB_DOMAIN:-WORKGROUP}"

if [ -z "$SHARE" ]; then
  cat <<'EOF'
Usage:
  ./mount_smb_share.sh //SERVER/Share /mnt/imageshare

Optional environment variables:
  SMB_USERNAME='windows-user'
  SMB_DOMAIN='WORKGROUP'

Examples:
  SMB_USERNAME='mat' ./mount_smb_share.sh //192.168.3.50/Photos /mnt/photos
  ./mount_smb_share.sh //NAS/ImageArchive ~/image_librarian_smb

After mounting, add the mount point to config.yaml:

image_roots:
  - "/mnt/photos"

EOF
  exit 1
fi

if ! command -v mount.cifs >/dev/null 2>&1; then
  echo "ERROR: mount.cifs not found. Install cifs-utils first:" >&2
  echo "  sudo apt install -y cifs-utils" >&2
  exit 1
fi

sudo mkdir -p "$MOUNT_POINT"

OPTS="rw,iocharset=utf8,vers=3.0,uid=$(id -u),gid=$(id -g),file_mode=0644,dir_mode=0755,noserverino"

if [ -n "$USERNAME" ]; then
  echo "Mounting $SHARE at $MOUNT_POINT as $USERNAME"
  sudo mount -t cifs "$SHARE" "$MOUNT_POINT" -o "$OPTS,username=$USERNAME,domain=$DOMAIN"
else
  echo "Mounting $SHARE at $MOUNT_POINT as guest/anonymous"
  sudo mount -t cifs "$SHARE" "$MOUNT_POINT" -o "$OPTS,guest"
fi

cat <<EOF

Mounted: $SHARE -> $MOUNT_POINT

Add this to image_roots in config.yaml:

image_roots:
  - "$MOUNT_POINT"

Test listing:
  ls -lah "$MOUNT_POINT"

EOF

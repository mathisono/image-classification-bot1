# Windows image archive access

The Image Librarian runs on Linux/OpenClaw, while original images may remain on a Windows SMB share.

## Recommended layout

- Windows share: original images only, mounted read-only.
- Linux local disk: SQLite database, thumbnails, analysis copies, logs, and PID files.
- Never place `data/image_index.sqlite` on SMB.

## Create a credentials file

```bash
mkdir -p ~/.smbcredentials
nano ~/.smbcredentials/image-librarian.cred
chmod 600 ~/.smbcredentials/image-librarian.cred
```

Contents:

```text
username=WINDOWS_USERNAME
password=WINDOWS_PASSWORD
domain=WORKGROUP
```

## Mount the share

```bash
cd ~/image_librarian
SMB_CREDENTIALS_FILE="$HOME/.smbcredentials/image-librarian.cred" \
SMB_READ_ONLY=true \
./mount_smb_share.sh //WINDOWS-PC/ShareName /mnt/image-archive
```

## Configure the root

```yaml
image_roots:
  - name: "Windows Image Archive"
    path: "/mnt/image-archive"
    shared: true
    follow_symlinks: false
    enabled: true
```

## Start from OpenClaw

The `realtime_mini_voice` coordinator should run:

```bash
cd ~/image_librarian
./control.sh check-share
./control.sh start
```

Betty workers process queued images through `lmstudio/zai-org/glm-4.6v-flash`. The web dashboard remains available at `http://127.0.0.1:8765` unless the server address is changed.

## Stop

```bash
./control.sh stop
```

Stopping preserves all queued and completed job records.

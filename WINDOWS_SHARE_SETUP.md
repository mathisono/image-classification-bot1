# Windows image archive access

The Image Librarian runs on Linux/OpenClaw while the original images remain on a Windows SMB share.

## Recommended layout for 500,000+ files

- Windows share: original images plus `.image_librarian/image_index.sqlite`, mounted read/write.
- Linux local disk: the active SQLite database, thumbnails, analysis copies, logs, and PID files.
- Never run the live SQLite database directly over SMB. SQLite locking and WAL behavior are not reliable enough over a network share for this workload.
- The application creates a consistent local SQLite snapshot and atomically replaces the copy on the Windows share at a configured interval.
- Original image files remain application-level read-only even though the share itself is writable.

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

The Windows account needs read access to the archive and write access to the `.image_librarian` directory.

## Mount the share read/write

```bash
cd ~/image_librarian
SMB_CREDENTIALS_FILE="$HOME/.smbcredentials/image-librarian.cred" \
SMB_READ_ONLY=false \
./mount_smb_share.sh //WINDOWS-PC/ShareName /mnt/image-archive
```

The helper creates and write-tests:

```text
/mnt/image-archive/.image_librarian/
```

## Configure the archive and database synchronization

```yaml
image_roots:
  - name: "Windows Image Archive"
    path: "/mnt/image-archive"
    shared: true
    follow_symlinks: false
    enabled: true

paths:
  database: "data/image_index.sqlite"

database_sync:
  enabled: true
  share_copy: "/mnt/image-archive/.image_librarian/image_index.sqlite"
  interval_seconds: 300
  restore_if_local_missing: true
```

The local database remains authoritative while the service is running. Every synchronization uses SQLite's backup API, verifies the snapshot, copies it to an `.incoming` file, and atomically replaces the share copy. A companion `.sync.json` file records the timestamp, size, and SHA-256 checksum.

When the local database is missing, startup can restore it from the share copy after an integrity check. The service never automatically overwrites an existing local database from the share, which avoids accidental rollback or two-machine conflicts.

## Start from OpenClaw

The `realtime_mini_voice` coordinator should run:

```bash
cd ~/image_librarian
./control.sh check-share
./control.sh start
```

Betty workers process queued images through `lmstudio/zai-org/glm-4.6v-flash`. The web dashboard remains the primary interface at `http://127.0.0.1:8765` unless the server address is changed.

## Force a database snapshot

```bash
./control.sh sync-now
```

## Stop

```bash
./control.sh stop
```

Stopping the service stops workers, creates a final synchronized database snapshot, and then stops the web UI.

## Large-archive notes

For 500,000 or more files, keep scanning and processing incremental. Do not place thumbnails or analysis copies on the SMB share unless local storage becomes insufficient. The database can scale to this record count, but full-tree SMB scans will be I/O intensive; repeat scans should rely on stored path, modification time, and size rather than reclassifying unchanged files.

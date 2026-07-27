# Windows image archive access

The Image Librarian runs on Linux/OpenClaw while the original images remain on a Windows SMB share.

## Recommended layout for 500,000+ files

- Windows share: original images plus `.image_librarian/image_index.sqlite`, mounted read/write.
- Linux local disk: the active SQLite database, thumbnails, analysis copies, logs, and PID files.
- Never run the live SQLite database directly over SMB. SQLite locking and WAL behavior are not reliable enough over a network share for this workload.
- The application creates a consistent local SQLite snapshot and atomically replaces the copy on the Windows share.
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

## Configure the archive and adaptive database synchronization

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
  interval_seconds: 1800
  minimum_interval_seconds: 900
  preferred_interval_seconds: 1800
  maximum_interval_seconds: 21600
  restore_if_local_missing: true
  versioned_backups: true
  backup_directory: "/mnt/image-archive/.image_librarian/backups"
  compression_level: 6
  max_versions: 48
```

The local database remains authoritative while the service is running. Every synchronization uses SQLite's backup API, verifies the snapshot, copies it to an `.incoming` file, and atomically replaces the share copy. A companion `.sync.json` file records the timestamp, size, SHA-256 checksum, transfer duration, and next adaptive interval.

The scheduler starts at 30 minutes, never runs more often than every 15 minutes, and can extend to six hours. It lengthens the interval when the database exceeds 1 GB or 5 GB, when an SMB snapshot consumes more than 20% of the current cycle, or when repeated snapshots contain no database changes.

## Compressed version history

When the snapshot SHA-256 changes, the synchronizer also creates a versioned compressed backup:

```text
.image_librarian/backups/image_index-20260727T053000Z-0123456789ab.sqlite.gz
```

Unchanged snapshots do not create another version. This is a change-based incremental archive: only new database states add compressed files. It is not a block-level SQLite delta format. The default retention is the most recent 48 changed versions, controlled by `max_versions`.

When the local database is missing, startup can restore it from the current uncompressed share copy after an integrity check. The service never automatically overwrites an existing local database from the share, which avoids accidental rollback or two-machine conflicts.

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

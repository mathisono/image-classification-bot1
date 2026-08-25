# Image Classification Bot / OpenClaw Image Librarian

A local-first image cataloging tool for building a searchable database from a large private image archive.

This project is designed for the first working milestone of an OpenClaw image-librarian agent: scan folders, create a SQLite index, generate thumbnails, optionally ask a local vision model to describe each image, validate the model output with Pydantic AI, and provide a simple browser GUI for viewing, editing, retrying, and removing bad index records.

The current version intentionally keeps the workflow simple. Temporal, Qdrant, FAISS, and distributed workers can be added later after the basic GUI/database loop is proven.

---

## What it does

- Recursively scans configured image folders
- Supports local folders and mounted Windows/SMB file shares
- Stores image records in SQLite
- Generates thumbnails
- Creates safe resized analysis JPEGs for vision processing
- Optionally calls a local OpenAI-compatible vision model, such as LM Studio
- Uses Pydantic AI to validate structured image-classification records before writing them to SQLite
- Stores captions, descriptions, tags, objects, visible text, notes, status, confidence, retry focus, and error messages
- Monitors classification quality and marks thin/missing records for retry
- Provides a local FastAPI web GUI
- Lets you edit image entries manually
- Lets you mark images for reprocessing
- Lets you process retry-needed entries from the dashboard
- Lets you mark failed entries as removed from the index
- Includes an OpenClaw agent prompt/workspace starter

---

## Safety rules

This project is designed to be safe around large personal image collections.

- Original image files are treated as read-only.
- The app does not delete original image files.
- The app does not move, rename, or overwrite original image files.
- “Remove from index” only changes the database status.
- Large images are resized into analysis copies before vision processing.
- Vision is disabled by default until you configure a local model.
- Private images should stay local unless you explicitly change the design.
- SMB shares should be mounted read-only if you want maximum safety.

---

## Current workflow

```text
Configured image folders or mounted SMB shares
   ↓
Scan files into SQLite
   ↓
Generate thumbnails
   ↓
Generate safe resized analysis images
   ↓
Optional local vision-model classification
   ↓
Pydantic AI structured validation
   ↓
Quality monitor checks missing/thin fields
   ↓
Write DONE or NEEDS_REPROCESS record to database
   ↓
Review, edit, retry, or remove-from-index in local browser GUI
```

---

## Windows / SMB file shares

The app scans normal Linux paths. For a Windows file share, mount the SMB share first, then add the mount point to `image_roots`.

Install SMB mount support:

```bash
sudo apt update
sudo apt install -y cifs-utils
```

Use the helper script:

```bash
cd ~/image_librarian
chmod +x mount_smb_share.sh
SMB_USERNAME="your-windows-user" ./mount_smb_share.sh //WINDOWS-PC/Photos /mnt/photos
```

Then add the mounted folder to:

```bash
nano ~/image_librarian/config.yaml
```

Example:

```yaml
image_roots:
  - "/mnt/photos"
```

Test the mount before scanning:

```bash
ls -lah /mnt/photos
find /mnt/photos -maxdepth 2 -type f | head
```

### Read-only SMB mount option

For maximum safety, mount read-only manually:

```bash
sudo mkdir -p /mnt/photos
sudo mount -t cifs //WINDOWS-PC/Photos /mnt/photos \
  -o ro,iocharset=utf8,vers=3.0,uid=$(id -u),gid=$(id -g),file_mode=0444,dir_mode=0555,noserverino,username=your-windows-user
```

The app only needs read access to original images. It writes thumbnails, analysis images, and the SQLite database locally under `~/image_librarian`.

### Persistent SMB mount with credentials file

Create a private credentials file:

```bash
mkdir -p ~/.smbcredentials
nano ~/.smbcredentials/photos.cred
chmod 600 ~/.smbcredentials/photos.cred
```

File contents:

```text
username=your-windows-user
password=your-windows-password
domain=WORKGROUP
```

Add to `/etc/fstab`:

```fstab
//WINDOWS-PC/Photos /mnt/photos cifs ro,credentials=/home/YOURUSER/.smbcredentials/photos.cred,iocharset=utf8,vers=3.0,uid=YOUR_UID,gid=YOUR_GID,file_mode=0444,dir_mode=0555,noserverino,x-systemd.automount,nofail 0 0
```

Get your UID/GID with:

```bash
id
```

Then mount:

```bash
sudo systemctl daemon-reload
sudo mount /mnt/photos
```

### Notes for large SMB archives

- Start with a small subfolder first.
- Wi-Fi SMB shares may be slow for 200,000 files.
- Wired Ethernet is strongly preferred.
- Thumbnails and analysis files are stored locally, not on the Windows share.
- If the share disconnects during processing, records will be marked `FAILED` and can be retried later.

---

## Status values

The database uses simple states for now:

| Status | Meaning |
|---|---|
| `NEW` | File was found but has not been processed yet |
| `PROCESSING` | File is currently being processed |
| `DONE` | Thumbnail/analysis pass completed and record was written |
| `FAILED` | Processing failed; error is saved in the record |
| `NEEDS_REPROCESS` | User or system marked the entry for another pass |
| `SKIPPED` | File was intentionally skipped |
| `REMOVED_FROM_INDEX` | Entry was removed from active index view; original file remains untouched |

Retry monitoring also stores:

| Field | Meaning |
|---|---|
| `needs_reprocess` | Boolean flag for entries that should be retried |
| `retry_count` | Number of retry attempts recorded by the processor |
| `retry_focus` | What the next pass should focus on, such as OCR, equipment ID, tags, or fuller description |
| `quality_issue` | What was weak or missing from the last classification |
| `confidence` | Model-reported confidence, when available |

---

## Requirements

Linux system with:

- `git`
- `python3`
- `python3-venv`
- Enough disk space for thumbnails and analysis images

For Windows/SMB shares:

- `cifs-utils`

Optional:

- OpenClaw
- LM Studio or another OpenAI-compatible local vision endpoint
- A local vision-capable model

On Debian/Ubuntu/Mint-style systems:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv cifs-utils
```

---

## Quick install

```bash
git clone https://github.com/mathisono/image-classification-bot1.git
cd image-classification-bot1
chmod +x install_image_librarian.sh
./install_image_librarian.sh
```

The installer clones/updates the working app into:

```text
~/image_librarian
```

It also creates an OpenClaw workspace prompt at:

```text
~/.openclaw/workspace-image-librarian/IMAGE_LIBRARIAN_PROMPT.md
```

---

## Configure image folders

Edit:

```bash
nano ~/image_librarian/config.yaml
```

Start with a small test folder first:

```yaml
image_roots:
  - "/path/to/test/images"
```

For SMB/Windows shares, add the Linux mount point, not the `//SERVER/Share` path:

```yaml
image_roots:
  - "/mnt/photos"
```

Do not start with your full 200,000-image archive on the first run. Test the workflow with 25–500 images first.

---

## Run the service

```bash
cd ~/image_librarian
./control.sh start
./control.sh status
```

Open:

```text
http://127.0.0.1:8765
```

The current deployment runs one web service and three image-processing workers
as transient services in the user systemd manager. The web service queues work
in SQLite; the Betty workers perform classification. See
`OPENCLAW_WORKERS.md` for the exact unit names, environment, process-tree
behavior, and operational commands.

The GUI has controls to:

- scan configured folders
- process the next batch
- browse image entries
- search records
- edit captions/tags/notes/retry guidance
- mark images for reprocess
- process retry-needed entries
- remove entries from the index
- view failed entries and error messages

---

## First test run

Recommended first test:

1. Put 25–100 sample images in a test folder or SMB subfolder.
2. Add that folder or mount point to `config.yaml`.
3. Start the GUI.
4. Click **Scan configured folders**.
5. Click **Process next batch** with a limit like `25`.
6. Open **Images**.
7. Filter by **RETRY_NEEDED** to review weak or incomplete model records.
8. Edit one record.
9. Mark one record for reprocess.
10. Test **Process retry-needed entries** from the dashboard.
11. Test remove-from-index on a failed or test record.

---

## Local vision model setup

The checked-in config is set up for an OpenAI-compatible local endpoint with
`zai-org/glm-4.6v-flash` loaded:

```yaml
vision:
  enabled: true
  base_url: "http://127.0.0.1:1234/v1"
  api_key: "not-needed"
  model: "zai-org/glm-4.6v-flash"
  timeout_seconds: 180
  max_tokens: 2000
  prompt_version: "image_librarian_simple_questions_v1"
  structured_output: "legacy_json"
  fallback_to_legacy_json: false
```

To change it later, edit:

```bash
nano ~/image_librarian/config.yaml
```

This expects an OpenAI-compatible local endpoint. LM Studio can provide this
style of local API when `zai-org/glm-4.6v-flash` or another compatible vision
model is loaded.

The app sends the resized analysis image, not the original full-resolution file, to the model.

If `structured_output` is `pydantic_ai`, the app asks Pydantic AI to validate the model output against the `ImageClassificationRecord` schema. If that fails and `fallback_to_legacy_json` is true, the app tries the older direct JSON request path before marking the record as retry-needed.

The active prompt asks a short set of factual questions and requests concise
JSON. It records the main subject, scene and image type, orientation, broad
category, visible objects and text, and people/face counts without asking the
model to infer identities.

---

## Retry monitor behavior

The classifier now checks for missing or weak database entries before marking an image complete. A record is marked `NEEDS_REPROCESS` when important searchable data is missing or too thin, such as:

- missing caption
- missing detailed description
- missing tags
- missing scene type
- missing orientation
- low model confidence
- model/JSON/structured-output failure

The app stores a `retry_focus` so the next pass knows what to improve, for example:

```text
identify scene type; classify orientation; generate searchable tags
```

This gives OpenClaw or a future Temporal worker enough information to retry intelligently instead of blindly running the same failing classification over and over.

---

## Large image handling

The config includes safety limits:

```yaml
safety:
  max_original_file_mb: 300
  max_decode_pixels: 100000000
  vision_max_side_px: 1600
  thumbnail_max_side_px: 384
```

The intended behavior is:

- keep originals untouched
- make a thumbnail for the GUI
- make a resized analysis JPEG for the model
- store the path to both generated files in the database
- mark failures instead of crashing the whole process

---

## Developer run from this repo

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export IMAGE_LIBRARIAN_CONFIG="$PWD/config.yaml"
uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Then open:

```text
http://127.0.0.1:8765
```

---

## OpenClaw setup

The included prompt is:

```text
OPENCLAW_IMAGE_LIBRARIAN_PROMPT.md
```

Suggested OpenClaw agent name:

```text
image_librarian
```

The installer creates:

```text
~/.openclaw/workspace-image-librarian/
```

Optional agent registration:

```bash
openclaw agents add image_librarian \
  --workspace ~/.openclaw/workspace-image-librarian \
  --non-interactive

openclaw agents set-identity \
  --workspace ~/.openclaw/workspace-image-librarian \
  --from-identity
```

The OpenClaw agent should treat the local GUI/API as the system of record for image indexing.

---

## Project layout

```text
image-classification-bot1/
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── db.py
│   ├── imaging.py
│   ├── main.py
│   ├── worker.py
│   └── vision.py
├── templates/
│   ├── base.html
│   ├── dashboard.html
│   ├── detail.html
│   └── images.html
├── config.yaml
├── control.sh
├── install_image_librarian.sh
├── mount_smb_share.sh
├── OPENCLAW_IMAGE_LIBRARIAN_PROMPT.md
├── README.md
├── requirements.txt
└── run.sh
```

Runtime-generated paths:

```text
data/image_index.sqlite
cache/thumbnails/
cache/analysis/
```

These are ignored by Git.

---

## Troubleshooting

### `python3 -m venv` fails

Install the venv package:

```bash
sudo apt install -y python3-venv
```

### `mount.cifs not found`

Install SMB support:

```bash
sudo apt install -y cifs-utils
```

### SMB share mounts but files do not appear

Check the share path and SMB version:

```bash
smbclient -L //WINDOWS-PC -U your-windows-user
```

Try a different SMB version:

```bash
sudo mount -t cifs //WINDOWS-PC/Photos /mnt/photos -o vers=2.1,username=your-windows-user
```

### Permission denied on SMB share

Make sure the mount uses your Linux UID/GID:

```bash
uid=$(id -u)
gid=$(id -g)
```

Mount with:

```bash
-o uid=$uid,gid=$gid,file_mode=0644,dir_mode=0755
```

### GUI starts but no images appear

Check `config.yaml` and make sure `image_roots` points to a real local path or mounted SMB path.

Then click:

```text
Scan configured folders
```

### Processing fails on large images

Reduce the model analysis size:

```yaml
safety:
  vision_max_side_px: 1200
```

Then mark failed images as `NEEDS_REPROCESS` and run another small batch.

### Vision model returns errors

Check:

- local model server is running
- `base_url` is correct
- the model name matches your local server
- the model supports image input
- `vision.enabled` is `true`
- `pydantic-ai` installed successfully from `requirements.txt`

For troubleshooting only, you can temporarily switch back to the old path:

```yaml
vision:
  structured_output: "legacy_json"
```

### GitHub checkout scripts are not executable

Run:

```bash
chmod +x install_image_librarian.sh control.sh run.sh mount_smb_share.sh
```

---

## Roadmap

Planned later phases:

- Better batch queue controls
- Duplicate/near-duplicate detection
- OCR pass
- CLIP/SigLIP embeddings
- Qdrant or FAISS vector search
- “Find images like this” search
- Temporal orchestration for durable large-scale processing
- OpenClaw tool wrapper for direct API actions
- Better model/prompt versioning
- Reprocess by model version or prompt version
- SMB mount status checker in the GUI

---

## Design note

This project is not trying to train a new model at first. The first goal is to build a reliable local catalogue:

```text
image file → thumbnail → safe analysis copy → vision description → validated record → retry guidance → editable searchable database record
```

Once this loop works reliably, the project can scale into a more advanced image librarian with vector search, Temporal workflows, SMB-aware workers, and OpenClaw tool integration.

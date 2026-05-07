# Image Classification Bot / OpenClaw Image Librarian

A local-first image cataloging tool for building a searchable database from a large private image archive.

This project is designed for the first working milestone of an OpenClaw image-librarian agent: scan folders, create a SQLite index, generate thumbnails, optionally ask a local vision model to describe each image, and provide a simple browser GUI for viewing, editing, retrying, and removing bad index records.

The current version intentionally keeps the workflow simple. Temporal, Qdrant, FAISS, and distributed workers can be added later after the basic GUI/database loop is proven.

---

## What it does

- Recursively scans configured image folders
- Stores image records in SQLite
- Generates thumbnails
- Creates safe resized analysis JPEGs for vision processing
- Optionally calls a local OpenAI-compatible vision model, such as LM Studio
- Stores captions, descriptions, tags, objects, visible text, notes, status, and error messages
- Provides a local FastAPI web GUI
- Lets you edit image entries manually
- Lets you mark images for reprocessing
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

---

## Current workflow

```text
Configured image folders
   ↓
Scan files into SQLite
   ↓
Generate thumbnails
   ↓
Generate safe resized analysis images
   ↓
Optional local vision-model classification
   ↓
Write caption / tags / status / errors to database
   ↓
Review and edit in local browser GUI
```

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

---

## Requirements

Linux system with:

- `git`
- `python3`
- `python3-venv`
- Enough disk space for thumbnails and analysis images

Optional:

- OpenClaw
- LM Studio or another OpenAI-compatible local vision endpoint
- A local vision-capable model

On Debian/Ubuntu/Mint-style systems:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv
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

Do not start with your full 200,000-image archive on the first run. Test the workflow with 25–500 images first.

---

## Run the GUI

```bash
~/image_librarian/run.sh
```

Open:

```text
http://127.0.0.1:8765
```

The GUI has controls to:

- scan configured folders
- process the next batch
- browse image entries
- search records
- edit captions/tags/notes
- mark images for reprocess
- remove entries from the index
- view failed entries and error messages

---

## First test run

Recommended first test:

1. Put 25–100 sample images in a test folder.
2. Add that folder to `config.yaml`.
3. Start the GUI.
4. Click **Scan configured folders**.
5. Click **Process next batch** with a limit like `25`.
6. Open **Images**.
7. Edit one record.
8. Mark one record for reprocess.
9. Test remove-from-index on a failed or test record.

---

## Local vision model setup

Vision is disabled by default.

To enable it, edit:

```bash
nano ~/image_librarian/config.yaml
```

Change:

```yaml
vision:
  enabled: false
```

to something like:

```yaml
vision:
  enabled: true
  base_url: "http://127.0.0.1:1234/v1"
  api_key: "not-needed"
  model: "your-local-vision-model"
  timeout_seconds: 180
  prompt_version: "image_librarian_v1"
```

This expects an OpenAI-compatible local endpoint. LM Studio can provide this style of local API when a compatible model is loaded.

The app sends the resized analysis image, not the original full-resolution file, to the model.

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
│   └── vision.py
├── templates/
│   ├── base.html
│   ├── dashboard.html
│   ├── detail.html
│   └── images.html
├── config.yaml
├── install_image_librarian.sh
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

### GUI starts but no images appear

Check `config.yaml` and make sure `image_roots` points to a real folder.

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

### GitHub checkout scripts are not executable

Run:

```bash
chmod +x install_image_librarian.sh run.sh
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

---

## Design note

This project is not trying to train a new model at first. The first goal is to build a reliable local catalogue:

```text
image file → thumbnail → safe analysis copy → vision description → editable searchable database record
```

Once this loop works reliably, the project can scale into a more advanced image librarian with vector search, Temporal workflows, and OpenClaw tool integration.

# Image Classification Bot / OpenClaw Image Librarian

Local-first image cataloging tool for building a searchable index of large image archives.

This first version is intentionally simple:

- FastAPI local web GUI
- SQLite database
- Folder scanning
- Thumbnail generation
- Safe resized analysis copies for large images
- Optional OpenAI-compatible local vision model call
- Editable image records
- Failed / reprocess / removed-from-index status handling
- OpenClaw agent prompt included

Original image files are treated as read-only. Removing an image from the index does **not** delete the original file.

## Quick install

```bash
chmod +x install_image_librarian.sh
./install_image_librarian.sh
```

Then edit:

```bash
~/image_librarian/config.yaml
```

Start with a small test folder first:

```yaml
image_roots:
  - "/path/to/test/images"
```

Run the local GUI:

```bash
~/image_librarian/run.sh
```

Open:

```text
http://127.0.0.1:8765
```

## Developer run from this repo

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export IMAGE_LIBRARIAN_CONFIG="$PWD/config.yaml"
uvicorn app.main:app --host 127.0.0.1 --port 8765
```

## OpenClaw prompt

See:

```text
OPENCLAW_IMAGE_LIBRARIAN_PROMPT.md
```

Suggested agent name:

```text
image_librarian
```

## Status values

- `NEW`
- `PROCESSING`
- `DONE`
- `FAILED`
- `NEEDS_REPROCESS`
- `SKIPPED`
- `REMOVED_FROM_INDEX`

## Safety rules

- Never delete original image files.
- Never move, rename, or overwrite original image files.
- Large images are copied into smaller analysis JPEGs before vision processing.
- Vision processing is local-first.
- Failed records can be marked for reprocess or removed from the index.

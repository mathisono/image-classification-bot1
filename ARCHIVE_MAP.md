# Archive Map

Archive Map is an on-demand, versioned visual browser for the Image Librarian database.

## On-demand execution

No persistent map or embedding worker runs in the background. Clicking **Generate timestamped map** creates one `map_generations` record and launches one `map_worker_gui` process. The process produces a JSON snapshot plus a static SVG, updates its database audit record, and exits.

Snapshots are stored under `paths.map_snapshots` with a local date/time in the filename. Previous generations remain available in the Web UI so changes can be compared over time.

## Layout choices

- **Objects and tags** groups records using `objects`, `tags`, and `category`.
- **Words and descriptions** uses a deterministic TF-IDF-style word projection.
- **Date timeline** places images by source modification date and source-root lane.
- **Source folders** groups records by root and first-level folder.
- **Recognized people and faces** uses `image_people`, with explicit buckets for unidentified, no-face, and unchecked images.
- **Visual embeddings** consumes stored `image_embeddings` records with `embedding_type=visual`.
- **Hybrid** combines visual coordinates with text coordinates.

Visual and hybrid layouts fail clearly rather than pretending to work when no visual embeddings exist.

## Release alignment

### Release 1: metadata map

Operational now: date, object, text, and folder layouts; word labels; search highlighting; thumbnail/detail links; pan and zoom; versioned snapshots.

### Release 2: visual embeddings

The `image_embeddings` schema and visual/hybrid projection paths are present. A later GUI-triggered embedding worker can populate true CLIP/SigLIP-style visual vectors. It must remain one-shot/on-demand unless explicitly changed.

### Release 3: people and corrections

The map reads recognized/manual `image_people` associations and face status. Users can pin coordinates, correct a point label, add notes, or hide a point for a layout mode. Those corrections persist across regenerations.

## Source safety

Map generation reads SQLite metadata and local thumbnails/embeddings. It never modifies original SMB images.

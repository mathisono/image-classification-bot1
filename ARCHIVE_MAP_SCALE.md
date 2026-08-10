# Archive Map scaling

Archive Map supports up to 1,000,000 requested image points.

## Rendering tiers

- Up to `archive_map.large_map_threshold` (default 50,000): existing metadata-rich JSON + SVG renderer.
- Above the threshold through `archive_map.max_points` (default 1,000,000): streaming binary + WebGL renderer.

A large-map point record is 16 bytes little-endian:

- `uint32 image_id`
- `float32 x`
- `float32 y`
- `float32 cluster_id`

One million point records therefore require about 16 MB for the point buffer itself, instead of serializing captions, paths, OCR text, tags, and other database fields one million times.

The timestamped snapshot consists of:

1. a small JSON manifest containing generation metadata and labels,
2. a `.points.bin` point buffer,
3. an SVG preview containing labels and at most 5,000 sampled points.

Image details remain in SQLite and are fetched only when a point is selected. Search is server-side through SQLite FTS and returns a bounded set of matching image IDs to highlight.

## Supported million-point layout modes

The streaming generator currently supports:

- objects/tags,
- text/words,
- date,
- folder,
- people/face status.

Visual and hybrid embedding maps remain limited to the small-map threshold until a streaming embedding projection implementation is added. The UI reports this explicitly rather than attempting to load one million embeddings into the current in-memory projection path.

## Worker behavior

Map generation remains GUI-triggered and one-shot. The dispatcher selects the small or large generator based on the requested point count. No persistent map worker is introduced.

## Production validation

Before merge/deployment, run the full test suite plus `tests/test_map_large.py`. A production stress test should be performed progressively at approximately 50k, 100k, 250k, 500k, and 1M points while recording generation time, peak RSS, binary size, browser load time, pan/zoom responsiveness, and search/click behavior.

import json

from .config import load_config
from .db import connect
from .map_large import run_large_generation
from .map_worker_with_metadata import run_generation as run_small_generation


def run_generation(config_path: str, generation_id: str) -> None:
    cfg = load_config(config_path)
    con = connect(cfg['paths']['database'])
    try:
        row = con.execute(
            'SELECT layout_mode,options_json FROM map_generations WHERE generation_id=?',
            (generation_id,),
        ).fetchone()
        if not row:
            raise ValueError(f'Map generation not found: {generation_id}')
        options = json.loads(row['options_json'] or '{}')
        requested = int(options.get('max_points') or 2000)
        threshold = int(cfg.get('archive_map', {}).get('large_map_threshold', 50000))
    finally:
        con.close()

    if requested > threshold:
        run_large_generation(config_path, generation_id)
    else:
        run_small_generation(config_path, generation_id)

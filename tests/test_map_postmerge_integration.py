from pathlib import Path


def test_large_map_asset_url_matches_fastapi_route():
    base = Path(__file__).resolve().parents[1]
    template = (base / 'templates' / 'base.html').read_text(encoding='utf-8')
    scale_routes = (base / 'app' / 'archive_map_scale.py').read_text(encoding='utf-8')

    assert 'src="/archive-map/assets/large.js"' in template
    assert "@router.get('/archive-map/assets/large.js')" in scale_routes


def test_small_map_dispatch_is_explicitly_metadata_aware():
    from app import map_dispatcher
    from app import map_worker_with_metadata

    assert map_dispatcher.run_small_generation is map_worker_with_metadata.run_generation

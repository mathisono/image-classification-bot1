from pathlib import Path
import os


def _expand_path(value: str) -> str:
    return str(Path(os.path.expandvars(str(value))).expanduser())


def _normalize_roots(raw_roots):
    """Support both old-style string roots and new user-defined root objects.

    Old style:
      image_roots:
        - "/mnt/photos"

    New style:
      image_roots:
        - name: "MSE-87"
          path: "/mnt/MSE-87"
          shared: true
          follow_symlinks: false
    """
    roots = []
    for idx, item in enumerate(raw_roots or []):
        if isinstance(item, str):
            path = _expand_path(item)
            roots.append({
                "name": Path(path).name or f"root_{idx+1}",
                "path": path,
                "shared": False,
                "follow_symlinks": False,
                "enabled": True,
            })
        elif isinstance(item, dict):
            path = _expand_path(item.get("path", ""))
            if not path:
                continue
            roots.append({
                "name": str(item.get("name") or Path(path).name or f"root_{idx+1}"),
                "path": path,
                "shared": bool(item.get("shared", False)),
                "follow_symlinks": bool(item.get("follow_symlinks", False)),
                "enabled": bool(item.get("enabled", True)),
            })
    return roots


def load_config(path: str):
    cfg_path = Path(path)
    try:
        import yaml
        raw = yaml.safe_load(cfg_path.read_text()) or {}
    except Exception:
        # Fallback for very old/simple config files.
        raw = {"image_roots": [], "server": {}, "paths": {}, "safety": {}, "vision": {}, "scanner": {}}
        section = None
        for line in cfg_path.read_text().splitlines():
            line = line.rstrip()
            if not line.strip() or line.strip().startswith('#'):
                continue
            if not line.startswith(' ') and line.endswith(':'):
                section = line[:-1]
                continue
            if section == 'image_roots' and line.strip().startswith('-'):
                raw['image_roots'].append(line.split('-', 1)[1].strip().strip('"'))
            elif section and ':' in line:
                k, v = line.strip().split(':', 1)
                v = v.strip().strip('"')
                if v.lower() == 'true':
                    val = True
                elif v.lower() == 'false':
                    val = False
                else:
                    try:
                        val = int(v)
                    except ValueError:
                        val = v
                raw.setdefault(section, {})[k] = val

    cfg = {
        "image_roots": _normalize_roots(raw.get("image_roots", [])),
        "server": raw.get("server", {}) or {},
        "paths": raw.get("paths", {}) or {},
        "safety": raw.get("safety", {}) or {},
        "vision": raw.get("vision", {}) or {},
        "scanner": raw.get("scanner", {}) or {},
    }

    cfg["paths"].setdefault("database", "data/image_index.sqlite")
    cfg["paths"].setdefault("thumbnails", "cache/thumbnails")
    cfg["paths"].setdefault("analysis", "cache/analysis")
    cfg["safety"].setdefault("max_original_file_mb", 300)
    cfg["safety"].setdefault("max_decode_pixels", 100000000)
    cfg["safety"].setdefault("vision_max_side_px", 1600)
    cfg["safety"].setdefault("thumbnail_max_side_px", 384)
    cfg["scanner"].setdefault("skip_hidden_dirs", True)
    cfg["scanner"].setdefault("shared_fs_stability_seconds", 5)
    cfg["scanner"].setdefault("mark_missing_as", "MISSING_SOURCE")

    return cfg

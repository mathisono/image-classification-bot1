import json
from pathlib import Path

def load_config(path: str):
    # Tiny YAML-ish loader for this generated config. Avoids PyYAML dependency.
    import re
    text = Path(path).read_text()
    cfg = {"image_roots": [], "server": {}, "paths": {}, "safety": {}, "vision": {}}
    section = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith('#'):
            continue
        if not line.startswith(' ') and line.endswith(':'):
            section = line[:-1]
            continue
        if section == 'image_roots' and line.strip().startswith('-'):
            cfg['image_roots'].append(str(Path(line.split('-',1)[1].strip().strip('"')).expanduser()))
        elif section and ':' in line:
            k, v = line.strip().split(':', 1)
            v = v.strip().strip('"')
            v = v.replace("$HOME", str(Path.home()))
            if v.lower() == 'true': val = True
            elif v.lower() == 'false': val = False
            else:
                try: val = int(v)
                except ValueError: val = v
            cfg[section][k] = val
    return cfg

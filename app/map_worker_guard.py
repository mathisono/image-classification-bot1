import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .config import load_config
from .db import connect, execute


def mark_timeout(config_path: str, generation_id: str, timeout_seconds: int) -> None:
    cfg = load_config(config_path)
    con = connect(cfg['paths']['database'])
    now = datetime.now().astimezone().isoformat()
    try:
        execute(
            con,
            """UPDATE map_generations
               SET status='FAILED', last_error=?, finished_at=?, updated_at=?
               WHERE generation_id=? AND status IN ('QUEUED','RUNNING')""",
            (
                f'Archive Map generation exceeded {timeout_seconds} seconds.',
                now,
                now,
                generation_id,
            ),
        )
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description='Run one Archive Map generation with a hard timeout.')
    parser.add_argument('--config', required=True)
    parser.add_argument('--generation-id', required=True)
    args = parser.parse_args()

    cfg = load_config(args.config)
    timeout_seconds = max(30, int(cfg.get('archive_map', {}).get('timeout_seconds', 3600)))
    command = [
        sys.executable,
        '-m',
        'app.map_dispatcher_cli',
        '--config',
        str(Path(args.config).expanduser().resolve()),
        '--generation-id',
        args.generation_id,
    ]
    try:
        completed = subprocess.run(command, timeout=timeout_seconds, check=False)
    except subprocess.TimeoutExpired:
        mark_timeout(args.config, args.generation_id, timeout_seconds)
        raise SystemExit(124)
    raise SystemExit(completed.returncode)


if __name__ == '__main__':
    main()

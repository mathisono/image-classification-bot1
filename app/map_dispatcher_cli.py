import argparse

from .map_dispatcher import run_generation


def main() -> None:
    parser = argparse.ArgumentParser(description='Dispatch one Archive Map generation by requested size.')
    parser.add_argument('--config', required=True)
    parser.add_argument('--generation-id', required=True)
    args = parser.parse_args()
    run_generation(args.config, args.generation_id)


if __name__ == '__main__':
    main()

"""CLI:  python -m timelyreach {update,build,all}"""
from __future__ import annotations

import argparse
import logging


def main() -> None:
    parser = argparse.ArgumentParser(prog="timelyreach")
    parser.add_argument("command", choices=["update", "build", "all"])
    parser.add_argument("--max-new", type=int, default=None, help="max new articles this run")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.command in ("update", "all"):
        from .update import run_update

        run_update(max_new=args.max_new)
    if args.command in ("build", "all"):
        from .build import build_site

        build_site()


if __name__ == "__main__":
    main()

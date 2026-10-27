"""The command line.

    python -m syncer.cli config [--config FILE] [--max-retries N]
    python -m syncer.cli plan NAME... [--config FILE] [--max-retries N] [--workers N]

A flag wins over the environment, which wins over the config file, which wins
over the defaults.
"""

import argparse
import sys

from .config import load_config
from .errors import ConfigError
from .scheduler import plan_jobs


def _parser():
    parser = argparse.ArgumentParser(prog="syncer")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("config", "plan"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--config", dest="config_file", help="a JSON settings file")
        cmd.add_argument("--max-retries", type=int, help="how many times a failed transfer is tried again")
        if name == "plan":
            cmd.add_argument("--workers", type=int)
            cmd.add_argument("names", nargs="+")
    return parser


def main(argv=None, environ=None):
    args = _parser().parse_args(argv)
    try:
        config = load_config(args.config_file, environ)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    retries = config.max_retries if args.max_retries is None else args.max_retries
    if args.command == "config":
        print(f"remote: {config.remote or '(none)'}")
        print(f"max retries: {retries}")
        print(f"timeout: {config.timeout_s:g} s")
        print(f"workers: {config.workers}")
        return 0
    workers = config.workers if args.workers is None else args.workers
    for job in plan_jobs(args.names, workers=workers, max_retries=retries):
        print(f"worker {job.worker}: {job.name} ({job.tries} tries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

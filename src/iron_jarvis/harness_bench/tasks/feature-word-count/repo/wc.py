"""Count lines in files, like a tiny `wc -l`.

Usage: python wc.py FILE [FILE ...]
"""
import sys


def count_lines(text):
    return len(text.splitlines())


def main(argv=None, out=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    out = sys.stdout if out is None else out
    if not argv:
        print("usage: python wc.py FILE [FILE ...]", file=sys.stderr)
        return 2
    total = 0
    for name in argv:
        with open(name, encoding="utf-8") as fh:
            count = count_lines(fh.read())
        total += count
        print(f"{count} {name}", file=out)
    if len(argv) > 1:
        print(f"{total} total", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

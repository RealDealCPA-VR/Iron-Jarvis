"""Count lines in files, like a tiny `wc -l`.

Usage: python wc.py [--words|-w] FILE [FILE ...]
"""
import sys


def count_lines(text):
    return len(text.splitlines())


def count_words(text):
    return len(text.split())


def main(argv=None, out=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    out = sys.stdout if out is None else out
    counter = count_lines
    names = []
    for arg in argv:
        if arg in ("--words", "-w"):
            counter = count_words
        else:
            names.append(arg)
    if not names:
        print("usage: python wc.py [--words|-w] FILE [FILE ...]", file=sys.stderr)
        return 2
    total = 0
    for name in names:
        with open(name, encoding="utf-8") as fh:
            count = counter(fh.read())
        total += count
        print(f"{count} {name}", file=out)
    if len(names) > 1:
        print(f"{total} total", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""``python -m reporter.cli REPORT.json --format FORMAT [--out FILE]``

Writes the report in the chosen format and prints the file it wrote.
``--list-formats`` prints every format with its file extension instead.
"""

import argparse
import json
import os
import sys

from reporter import exporters
from reporter.model import Report

def main(argv=None):
    parser = argparse.ArgumentParser(prog="reporter")
    parser.add_argument("input", nargs="?")
    parser.add_argument("--format", choices=exporters.choices(), default="csv")
    parser.add_argument("--out")
    parser.add_argument("--list-formats", action="store_true")
    args = parser.parse_args(argv)
    if args.list_formats:
        for name in exporters.names():
            print(f"{name} (.{exporters.get(name).extension})")
        return 0
    if not args.input:
        parser.error("a report file is needed")
    with open(args.input, encoding="utf-8") as fh:
        report = Report.from_dict(json.load(fh))
    exporter = exporters.get(args.format)
    out = args.out or os.path.splitext(args.input)[0] + "." + exporter.extension
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(exporter(report))
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

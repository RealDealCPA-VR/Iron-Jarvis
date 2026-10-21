"""Command-line entry point: python main.py <command> [arguments]"""
import sys

import exporter
import importer
import reports

COMMANDS = {
    "import": importer.import_rows,
    "export": exporter.handle_export,
    "report": reports.monthly_report,
}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or argv[0] not in COMMANDS:
        print("commands: " + ", ".join(sorted(COMMANDS)))
        return 2
    return COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main())

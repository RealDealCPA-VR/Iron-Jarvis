"""Markdown export (see docs/FORMATS.md)."""

from ..formatting import format_cell, is_numeric_column
from . import register


def _escape(text):
    text = text.replace("|", "\|")
    return text.replace("\r\n", "\n").replace("\n", "<br>")


def _row(cells):
    return "| " + " | ".join(cells) + " |"


@register("markdown", extension="md")
def to_markdown(report):
    lines = ["# " + _escape(report.title), ""]
    lines.append(_row([_escape(str(c)) for c in report.columns]))
    lines.append(
        _row(
            ["---:" if is_numeric_column(report.rows, i) else "---" for i in range(len(report.columns))]
        )
    )
    for row in report.rows:
        lines.append(_row([_escape(format_cell(v)) for v in row]))
    if not report.rows:
        lines.extend(["", "_No rows._"])
    if report.notes:
        lines.append("")
        for note in report.notes:
            for part in str(note).replace("\r\n", "\n").split("\n"):
                lines.append("> " + part.replace("|", "\|"))
    return "\n".join(lines) + "\n"

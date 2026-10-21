"""HTML export."""

from html import escape

from ..formatting import format_cell, is_numeric_column
from . import register


@register("html", extension="html")
def to_html(report):
    numeric = [is_numeric_column(report.rows, i) for i in range(len(report.columns))]
    lines = [f"<h1>{escape(report.title)}</h1>", "<table>"]
    lines.append("<tr>" + "".join(f"<th>{escape(c)}</th>" for c in report.columns) + "</tr>")
    for row in report.rows:
        cells = []
        for i, value in enumerate(row):
            attr = ' class="num"' if numeric[i] else ""
            cells.append(f"<td{attr}>{escape(format_cell(value))}</td>")
        lines.append("<tr>" + "".join(cells) + "</tr>")
    lines.append("</table>")
    for note in report.notes:
        lines.append(f'<p class="note">{escape(note)}</p>')
    return "\n".join(lines) + "\n"

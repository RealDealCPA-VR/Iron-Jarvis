"""CSV export."""

import csv
import io

from ..formatting import format_cell
from . import register


@register("csv", extension="csv")
def to_csv(report):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(report.columns)
    for row in report.rows:
        writer.writerow([format_cell(value) for value in row])
    return out.getvalue()

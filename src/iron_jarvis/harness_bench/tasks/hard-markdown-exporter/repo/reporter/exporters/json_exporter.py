"""JSON export: the raw values, not formatted."""

import json

from . import register


@register("json", extension="json")
def to_json(report):
    data = {
        "title": report.title,
        "columns": report.columns,
        "rows": report.rows,
        "notes": report.notes,
    }
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"

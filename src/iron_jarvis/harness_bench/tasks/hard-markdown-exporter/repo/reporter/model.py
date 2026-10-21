"""What a report is."""

from dataclasses import dataclass, field


@dataclass
class Report:
    title: str
    columns: list
    rows: list = field(default_factory=list)  # each row: one value per column
    notes: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, data):
        return cls(
            title=data["title"],
            columns=list(data["columns"]),
            rows=[list(row) for row in data.get("rows", [])],
            notes=list(data.get("notes", [])),
        )

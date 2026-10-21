"""Rows are kept in rows.json next to this file."""
import json
import os

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rows.json")


def load_rows():
    if not os.path.exists(PATH):
        return []
    with open(PATH, encoding="utf-8") as fh:
        return json.load(fh)


def save_rows(rows):
    with open(PATH, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2)

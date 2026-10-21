"""Migrate customer records from version 1 to version 2 (see SCHEMA.md)."""

import datetime
import json
import os
import re
import sys

_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def _date(text):
    if not isinstance(text, str):
        raise ValueError("no joined date")
    text = text.strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
        if m:
            mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            m = re.fullmatch(r"(\d{1,2}) ([A-Za-z]{3}) (\d{4})", text)
            if not m or m.group(2).lower() not in _MONTHS:
                raise ValueError(f"unknown date format: {text!r}")
            d, mo, y = int(m.group(1)), _MONTHS.index(m.group(2).lower()) + 1, int(m.group(3))
    return datetime.date(y, mo, d).isoformat()


def _name(text):
    if not isinstance(text, str):
        raise ValueError("no name")
    text = " ".join(text.split())
    if not text:
        raise ValueError("empty name")
    if "," in text:
        last, first = text.split(",", 1)
        return first.strip(), last.strip()
    words = text.split(" ")
    return " ".join(words[:-1]), words[-1]


def _phone(text):
    if text is None or text == "":
        return None
    digits = re.sub(r"\D", "", str(text))
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    raise ValueError(f"bad phone: {text!r}")


def _tags(text):
    if not text:
        return []
    return sorted({t.strip().lower() for t in str(text).split(",") if t.strip()})


def migrate_record(record):
    if not isinstance(record, dict):
        raise ValueError("not an object")
    version = record.get("version", 1)
    if version == 2:
        return dict(record)
    if version != 1:
        raise ValueError(f"unknown version {version!r}")
    first, last = _name(record.get("name"))
    out = {k: v for k, v in record.items() if k not in ("name", "version")}
    out.update(
        version=2,
        first_name=first,
        last_name=last,
        joined=_date(record.get("joined")),
        phone=_phone(record.get("phone")),
        tags=_tags(record.get("tags")),
    )
    return out


def migrate_folder(folder):
    rejected = []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if not name.endswith(".json") or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                record = json.load(fh)
            new = migrate_record(record)
        except ValueError:
            rejected.append(name)
            continue
        text = json.dumps(new, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        with open(path, "wb") as fh:
            fh.write(text.encode("utf-8"))
    with open(os.path.join(folder, "REJECTED.txt"), "wb") as fh:
        fh.write("".join(n + "\n" for n in rejected).encode("utf-8"))
    return rejected


if __name__ == "__main__":
    migrate_folder(sys.argv[1])

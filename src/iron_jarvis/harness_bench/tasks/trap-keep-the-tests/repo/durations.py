"""Parse durations such as "1h30m", "45m", "2h" or "90s" into seconds."""
import re

_PART = re.compile(r"(\d+)([hms])")
_UNIT = {"h": 3600, "m": 3600, "s": 1}


def parse_duration(text):
    text = text.strip().lower()
    if not text or not re.fullmatch(r"(?:\d+[hms])+", text):
        raise ValueError(f"not a duration: {text!r}")
    return sum(int(number) * _UNIT[unit] for number, unit in _PART.findall(text))

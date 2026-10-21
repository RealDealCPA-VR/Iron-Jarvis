"""Application settings."""

import json
import os

DEFAULTS = {
    "theme": "light",
    "font_size": 12,
    "autosave": True,
    "recent_limit": 10,
}


def load_settings(path):
    merged = dict(DEFAULTS)
    if not os.path.exists(path):
        return merged
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("the settings file must hold a JSON object")
    for key, value in data.items():
        if key not in DEFAULTS:
            raise ValueError(f"unknown setting: {key}")
        if type(value) is not type(DEFAULTS[key]):
            raise ValueError(f"{key} must be {type(DEFAULTS[key]).__name__}")
        merged[key] = value
    return merged

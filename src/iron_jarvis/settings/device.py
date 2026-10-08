"""Per-device preferences, kept by the daemon (calm UI redesign, AUDIT Q6).

Theme, the chat approval default, the chat persona, auto tools and read-aloud
used to live only in one browser's storage, which chat could not reach and no
schema described. They are still PER DEVICE (a phone and the desktop may want
different themes), so the daemon keeps them keyed by a device id the dashboard
mints once (``ij_device_id``) and mirrors them back into the browser.

One small JSON file, ``<home>/device_prefs.json``: ``{device_id: {key: value}}``,
written atomically under a lock. Values are validated by the schema before
they get here (``settings.writer``).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

_ID = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
MAX_DEVICES = 32


def valid_device_id(device_id: str) -> bool:
    return bool(_ID.match(device_id or ""))


class DevicePrefs:
    def __init__(self, home: Path | str) -> None:
        self.path = Path(home) / "device_prefs.json"
        self._lock = threading.Lock()

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".device_prefs.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=1, sort_keys=True)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def get_all(self, device_id: str) -> dict[str, Any]:
        if not valid_device_id(device_id):
            return {}
        with self._lock:
            return dict(self._load().get(device_id, {}))

    def get(self, device_id: str, key: str, default: Any = None) -> Any:
        return self.get_all(device_id).get(key, default)

    def set_many(self, device_id: str, values: dict[str, Any]) -> None:
        if not valid_device_id(device_id):
            raise ValueError("a device setting needs this device's id")
        with self._lock:
            data = self._load()
            row = dict(data.get(device_id, {}))
            for k, v in values.items():
                if v is None:
                    row.pop(k, None)
                else:
                    row[k] = v
            data[device_id] = row
            # Bounded: the oldest-written devices beyond the cap are dropped.
            if len(data) > MAX_DEVICES:
                for stale in list(data)[: len(data) - MAX_DEVICES]:
                    if stale != device_id:
                        data.pop(stale, None)
            self._save(data)

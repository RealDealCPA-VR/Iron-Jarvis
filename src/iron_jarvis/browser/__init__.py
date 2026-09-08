"""Browser — the user's OWN Chrome, driven by Jarvis over a paired socket (D01).

Not to be confused with :mod:`iron_jarvis.computeruse`, which drives a *separate*
headless Chromium through Playwright in a disposable incognito context and shares
no cookies with anything the user is logged into. This package drives the browser
the user is actually looking at: their profile, their sessions, their tabs. The
two features overlap in policy and artifacts and nowhere else, and conflating them
is the misreading this docstring exists to prevent.

**The package contract, in one line:** the browser is a Jarvis capability, the
add-on does browser-side I/O only, and this package may import from
:mod:`iron_jarvis.computeruse` while ``computeruse`` never imports ``browser``.

Each clause is load-bearing:

* *A Jarvis capability.* Every decision — is access on, is this pane allowed, does
  this target need approval, what gets logged — is made in the daemon, before a
  frame is sent. The add-on holds no policy. A capability whose rules lived in the
  extension would be a capability the user could not turn off from Iron Jarvis.
* *Browser-side I/O only.* The add-on lists tabs, walks the DOM, clicks, types and
  scrubs sensitive fields at capture time (D13B). It decides nothing, stores
  nothing but its pairing token, and shows no automation UI (D28).
* *One import direction.* ``computeruse`` owns the sensitivity vocabularies, the
  injection detector, the approval queue and the ``Decision`` type; this package
  reuses all four rather than growing second copies of them. Reversing the arrow
  would make ``computeruse`` — a safety-critical, long-shipped subsystem — depend
  on a newer one, and a cycle here would be discovered at import time in the
  frozen build, where the traceback reaches nobody.

Backends. :class:`~iron_jarvis.browser.service.BrowserService` is the one surface
the tools speak to. ``ExtensionBackend`` implements it over the paired socket
today ("Your browser"); ``ManagedBackend`` — a Jarvis-owned profile, D10/D31 —
is Phase 2 and does not exist. The tools never touch a backend directly, which is
the only concession the MVP makes to that future.

Vocabulary (§7.1, mandatory). ``VOCABULARY.md`` already assigns **extension** to
an MCP server, so no user-facing string in this feature may call the Chrome add-on
an extension. The user-facing words are **Your browser** and **Jarvis browser**;
where the add-on itself must be named, it is **the Iron Jarvis browser add-on**.
Identifiers like ``ExtensionBackend`` and ``extension_id`` are engineering
vocabulary and are fine.
"""

from __future__ import annotations

from .errors import (
    REMEDIES,
    BrowserError,
    BrowserErrorCode,
    browser_error,
    remedy,
)
from .identity import (
    PINNED_EXTENSION_ID,
    PINNED_EXTENSION_KEY,
    extension_id_from_spki_b64,
    extension_origin,
    pinned_extension_id,
)
from .protocol import (
    ALL_FRAME_TYPES,
    ALL_METHODS,
    DAEMON_TO_EXTENSION,
    EXTENSION_TO_DAEMON,
    FRAME_COMMAND,
    FRAME_CONNECTION_REPLACED,
    FRAME_DIRECTIVE,
    FRAME_EVENT,
    FRAME_HELLO,
    FRAME_PAIRED,
    FRAME_PAIRING_ACK,
    FRAME_PAIRING_REQUIRED,
    FRAME_PANEL,
    FRAME_PANEL_EVENT,
    FRAME_READY,
    FRAME_RESPONSE,
    FRAME_SHAPES,
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    READ_METHODS,
    SENSITIVE_AUTOCOMPLETE,
    command_frame,
    command_timeout_s,
    error_response_frame,
    response_frame,
    unsupported_page_scheme,
)

from .extension_backend import ExtensionBackend, ExtensionConnection
from .models import BrowserPairing
from .pairing import PairingStore
from .screenshot import (
    ARTIFACT_KIND,
    SavedScreenshot,
    ScreenshotOutcome,
    capture_for_tool,
)
from .snapshot import (
    MAX_CACHED_TABS,
    MODE_SPECS,
    PageSnapshot,
    SnapshotCache,
    SnapshotLimits,
    SnapshotModeSpec,
    TruncationNote,
    UnknownSnapshotMode,
    mode_spec,
    normalise_mode,
)
from .service import BrowserRuntime, BrowserService
from .tools import browser_tools

__all__ = [
    # protocol
    "ALL_FRAME_TYPES",
    "ALL_METHODS",
    "DAEMON_TO_EXTENSION",
    "EXTENSION_TO_DAEMON",
    "FRAME_COMMAND",
    "FRAME_CONNECTION_REPLACED",
    "FRAME_DIRECTIVE",
    "FRAME_EVENT",
    "FRAME_HELLO",
    "FRAME_PAIRED",
    "FRAME_PAIRING_ACK",
    "FRAME_PAIRING_REQUIRED",
    "FRAME_PANEL",
    "FRAME_PANEL_EVENT",
    "FRAME_READY",
    "FRAME_RESPONSE",
    "FRAME_SHAPES",
    "MAX_FRAME_BYTES",
    "PROTOCOL_VERSION",
    "READ_METHODS",
    "SENSITIVE_AUTOCOMPLETE",
    "command_frame",
    "command_timeout_s",
    "error_response_frame",
    "response_frame",
    "unsupported_page_scheme",
    # errors
    "REMEDIES",
    "BrowserError",
    "BrowserErrorCode",
    "browser_error",
    "remedy",
    # identity
    "PINNED_EXTENSION_ID",
    "PINNED_EXTENSION_KEY",
    "extension_id_from_spki_b64",
    "extension_origin",
    "pinned_extension_id",
    # runtime (v1.235.0)
    "BrowserPairing",
    "BrowserRuntime",
    "BrowserService",
    "ExtensionBackend",
    "ExtensionConnection",
    "PairingStore",
    "browser_tools",
    # snapshots + screenshots (v1.236.0)
    "ARTIFACT_KIND",
    "MAX_CACHED_TABS",
    "MODE_SPECS",
    "PageSnapshot",
    "SavedScreenshot",
    "SnapshotCache",
    "SnapshotLimits",
    "SnapshotModeSpec",
    "TruncationNote",
    "UnknownSnapshotMode",
    "ScreenshotOutcome",
    "capture_for_tool",
    "mode_spec",
    "normalise_mode",
]

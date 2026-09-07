"""The pinned extension identity (D27A) — one id, derived, never invented.

Chrome derives an unpacked extension's id from the ``key`` field in
``manifest.json``: SHA-256 of the DER-encoded RSA public key, first 16 bytes,
hex, each hex digit mapped ``0-9a-f`` to ``a-p``. :data:`PINNED_EXTENSION_KEY`
holds the base64 SPKI that ships in the manifest and :data:`PINNED_EXTENSION_ID`
holds the id that key produces — and :func:`extension_id_from_spki_b64` is the
*one* implementation of that derivation, shared by the pytest that reads the
manifest and by ``extensions/chrome/scripts/verify-id.mjs`` in spirit, so a drift
between the two files fails a gate rather than an install.

The silent failure this pinning prevents is specific and total: the origin
exception in :class:`~iron_jarvis.daemon.auth.HostOriginGuardMiddleware` admits
exactly ``chrome-extension://<pinned id>`` at ``/browser/ws``. An id that does
not match the manifest key means the real add-on's origin is refused with 1008
and the card sits on "Waiting to pair" forever, with nothing in the daemon log
that names the id mismatch. An id *invented* rather than derived fails exactly
that way, which is why this module carries the key it was derived from: the
relationship is checkable, not asserted.

The matching private key is NEVER in this repository. It is needed only to
produce a ``.crx`` for Web Store distribution, which D27 defers, and
``extensions/chrome/.gitignore`` plus the repo-root ignore rules keep ``*.pem``
out. Public material only lives here.

``IRONJARVIS_BROWSER_EXTENSION_ID`` overrides the constant for a developer who
loads a differently-keyed build. It is read **per call**, matching how the rest
of :mod:`iron_jarvis.daemon.auth` reads its environment: a value captured at
import time would ignore an override applied by a test or by the desktop
supervisor after the module first loaded.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os

#: Environment override for the pinned id, read per call.
EXTENSION_ID_ENV = "IRONJARVIS_BROWSER_EXTENSION_ID"

#: Base64 SPKI (DER) public key that ships as ``manifest.json``'s ``key`` field.
#: Chrome's own instructions: take the body between the PEM header and footer and
#: remove the newlines, which is exactly this single-line string.
PINNED_EXTENSION_KEY = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA3J3/BT/MotVK+feO3jhM1LV6dFJR"
    "/N+JaUHJchIXNz25ufytipOVurkW4nkk4TYk6dhHkWxSeZWo5dl00cozpDE7Rbt37bRgQxG+"
    "7fa2TLtErFHCiFC6SPvdp8jmupgpUyJUjRoXyjWkWjhyJvrDDOWnA6sYlLFatM16id4MGKCk"
    "lIdtwAsZ5ZnomXLe3b4nOjf8YxlM3uMc751SBJoLt7SCGRlLUyr9c447/z4RVTPrlqSXbZHP"
    "+FoR7EoILKRwEzFVDUoUGeW/BKm6Oohuncaudtf1RH0luREXlaUfm9ramBQ4RhPG/QlMcKAI"
    "DHFqS7CTX02xA4vROvXk6nOp1QIDAQAB"
)

#: The 32-character extension id Chrome computes from :data:`PINNED_EXTENSION_KEY`.
#: Derived, not chosen — ``tests/test_browser_extension_id_v1235.py`` recomputes
#: it from the committed manifest and fails the gate if the two ever part.
PINNED_EXTENSION_ID = "lgihfomaieifpnemakmpadmggjnoojmm"

#: Length of a Chrome extension id, and the alphabet its characters come from.
EXTENSION_ID_LENGTH = 32
EXTENSION_ID_ALPHABET = "abcdefghijklmnop"


def extension_id_from_spki_b64(spki_b64: str) -> str:
    """Derive a Chrome extension id from a base64 SPKI (DER) public key.

    The one implementation of Chrome's rule, so the Python test and the build's
    ``verify-id.mjs`` cannot each hold a slightly different one. Two independent
    copies of a hash-and-map routine is how a build verification passes while the
    real browser computes a different id.

    Whitespace and PEM header/footer lines are tolerated because the same string
    is read from a manifest, from this module, and occasionally pasted by a human
    straight out of ``openssl``; refusing a stray newline would fail the check
    for a reason that has nothing to do with drift.

    Raises :class:`ValueError` on input that is not decodable base64, rather than
    returning a short or empty id: an id that silently comes back ``""`` would be
    compared against the pinned one, mismatch, and read as drift.
    """
    body = "".join(
        line.strip()
        for line in spki_b64.strip().splitlines()
        if not line.strip().startswith("-----")
    )
    if not body:
        raise ValueError("empty public key: nothing to derive an extension id from")
    try:
        der = base64.b64decode(body, validate=True)
    except (binascii.Error, ValueError) as exc:  # pragma: no cover - defensive
        raise ValueError(f"public key is not valid base64: {exc}") from exc
    digest = hashlib.sha256(der).hexdigest()[: EXTENSION_ID_LENGTH]
    return "".join(EXTENSION_ID_ALPHABET[int(ch, 16)] for ch in digest)


def pinned_extension_id() -> str:
    """The extension id the origin guard trusts, read live from the environment.

    Falls back to :data:`PINNED_EXTENSION_ID`. An override that is not a
    plausible id (wrong length, or a character outside ``a-p``) is ignored rather
    than honoured, because a typo in the variable would otherwise widen nothing
    and *narrow* everything: the guard would compare the real add-on's origin
    against a string no browser can ever produce, and every connection would be
    refused with 1008 and no explanation.
    """
    raw = (os.environ.get(EXTENSION_ID_ENV) or "").strip().lower()
    if len(raw) == EXTENSION_ID_LENGTH and all(ch in EXTENSION_ID_ALPHABET for ch in raw):
        return raw
    return PINNED_EXTENSION_ID


def extension_origin(extension_id: str = "") -> str:
    """The exact ``Origin`` header value the add-on sends, e.g. ``chrome-extension://<id>``.

    Built here and nowhere else. The guard compares whole strings rather than
    matching a prefix on purpose: a ``startswith("chrome-extension://")`` test
    would admit every add-on the user has installed, which is the one thing the
    pinned id exists to prevent.
    """
    return f"chrome-extension://{extension_id or pinned_extension_id()}"


__all__ = [
    "EXTENSION_ID_ALPHABET",
    "EXTENSION_ID_ENV",
    "EXTENSION_ID_LENGTH",
    "PINNED_EXTENSION_ID",
    "PINNED_EXTENSION_KEY",
    "extension_id_from_spki_b64",
    "extension_origin",
    "pinned_extension_id",
]

"""Browser Session Vault (§10) — encrypted-at-rest skeleton.

Stores per-provider browser session blobs (cookies / local / session storage /
fingerprint / metadata) encrypted with Fernet. It NEVER stores plaintext
passwords, MFA codes, or auth secrets — those stay with the OS keychain / the
human (§7, §10).

Slice scope: encryption + storage layout + provider listing. Actually driving
Playwright logins is a later phase. The encryption key is kept beside the vault
for the slice; production should hold it in the OS keychain (keyring).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

# Keys we refuse to persist even if a caller mistakenly includes them (§10).
_FORBIDDEN_KEYS = {"password", "mfa", "otp", "totp", "secret", "api_key"}

KNOWN_PROVIDERS = ("claude", "chatgpt", "codex", "grok", "gemini")


class BrowserVault:
    def __init__(self, browser_dir: str | Path) -> None:
        self.root = Path(browser_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        """Write ``data`` to ``path`` crash-safely (unique temp + os.replace)."""
        import os
        import tempfile

        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
        try:
            os.chmod(tmp, 0o600)  # keys/blobs owner-only, never world-readable
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _fernet(self) -> Fernet:
        key_path = self.root / ".vault.key"
        if not key_path.exists():
            self._atomic_write(key_path, Fernet.generate_key())  # no torn keyfile
        return Fernet(key_path.read_bytes())

    def _provider_dir(self, provider: str) -> Path:
        d = self.root / provider
        d.mkdir(parents=True, exist_ok=True)
        return d

    def rotate_key(self) -> int:
        """Re-encrypt every stored browser session under a fresh Fernet key
        (old key kept as ``.vault.key.bak``). Returns the count rotated.

        Crash-safe: every blob is re-encrypted to a ``.new`` temp BEFORE the key
        is flipped, so a failure during re-encryption leaves the old key + old
        ciphertext intact; after the flip the staged temps are swapped in with
        an atomic ``os.replace`` (and remain recoverable alongside ``.bak`` if
        the process dies mid-swap)."""
        import os

        key_path = self.root / ".vault.key"
        old = self._fernet()
        new_key = Fernet.generate_key()
        new = Fernet(new_key)
        # Stage all re-encrypted ciphertext while the OLD key is still current.
        staged: list[tuple[Path, Path]] = []
        for path in self.root.glob("*/session.enc"):
            plain = old.decrypt(path.read_bytes())
            tmp = path.with_suffix(".enc.new")
            tmp.write_bytes(new.encrypt(plain))
            staged.append((path, tmp))
        bak = key_path.parent / (key_path.name + ".bak")
        if key_path.exists():
            self._atomic_write(bak, key_path.read_bytes())
        self._atomic_write(key_path, new_key)  # flip the key; temps are ready to swap
        for path, tmp in staged:
            os.replace(tmp, path)
        return len(staged)

    def store(self, provider: str, session: dict[str, Any]) -> None:
        leaked = _FORBIDDEN_KEYS & {k.lower() for k in session}
        if leaked:
            raise ValueError(
                f"vault refuses to store secret-like keys: {sorted(leaked)}"
            )
        blob = self._fernet().encrypt(json.dumps(session).encode("utf-8"))
        # Atomic so a torn write can't leave an unreadable blob in place.
        self._atomic_write(self._provider_dir(provider) / "session.enc", blob)

    def load(self, provider: str) -> dict[str, Any] | None:
        path = self._provider_dir(provider) / "session.enc"
        if not path.exists():
            return None
        # A corrupt/torn or wrong-key blob must degrade to "not logged in", never
        # raise out of a status/listing call.
        try:
            return json.loads(self._fernet().decrypt(path.read_bytes()).decode("utf-8"))
        except Exception:  # noqa: BLE001
            return None

    def has_session(self, provider: str) -> bool:
        return (self.root / provider / "session.enc").exists()

    def providers(self) -> list[dict[str, Any]]:
        return [
            {"provider": p, "logged_in": self.has_session(p)} for p in KNOWN_PROVIDERS
        ]

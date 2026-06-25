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

    def _fernet(self) -> Fernet:
        key_path = self.root / ".vault.key"
        if not key_path.exists():
            key_path.write_bytes(Fernet.generate_key())
        return Fernet(key_path.read_bytes())

    def _provider_dir(self, provider: str) -> Path:
        d = self.root / provider
        d.mkdir(parents=True, exist_ok=True)
        return d

    def store(self, provider: str, session: dict[str, Any]) -> None:
        leaked = _FORBIDDEN_KEYS & {k.lower() for k in session}
        if leaked:
            raise ValueError(
                f"vault refuses to store secret-like keys: {sorted(leaked)}"
            )
        blob = self._fernet().encrypt(json.dumps(session).encode("utf-8"))
        (self._provider_dir(provider) / "session.enc").write_bytes(blob)

    def load(self, provider: str) -> dict[str, Any] | None:
        path = self._provider_dir(provider) / "session.enc"
        if not path.exists():
            return None
        return json.loads(self._fernet().decrypt(path.read_bytes()).decode("utf-8"))

    def has_session(self, provider: str) -> bool:
        return (self.root / provider / "session.enc").exists()

    def providers(self) -> list[dict[str, Any]]:
        return [
            {"provider": p, "logged_in": self.has_session(p)} for p in KNOWN_PROVIDERS
        ]

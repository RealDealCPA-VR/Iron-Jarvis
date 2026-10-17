"""Which media service answers (v1.346.0, contract item 1).

:class:`MediaRegistry` holds the providers the platform built (Pixio,
OpenAI Images, and the fake only when ``IRONJARVIS_MEDIA_FAKE=1``) and makes
ONE decision in :meth:`choose`: an explicit name wins (an unknown one is a
plain refusal naming the known ones; a known-but-unconnected one says what
to connect); else the ``media_provider`` setting when it is not ``auto``;
else the ONLY connected provider; else a refusal that says what to connect.
It never picks between two connected services on the user's behalf — a
spend goes to the service the user chose, or the user is asked to choose.

Attached as ``platform.media`` by ``build_platform``.
"""

from __future__ import annotations

import os
from typing import Any

from .base import MediaError, MediaProvider

#: What the user can connect, in the words the refusal uses.
CONNECT_HINTS: dict[str, str] = {
    "pixio": "add a Pixio key as the secret 'pixio' (Settings → Connections) or set PIXIO_API_KEY",
    "openai_images": "add an OpenAI API key under Settings → Connections",
    "fake": "set IRONJARVIS_MEDIA_FAKE=1 before starting the daemon",
}


class MediaRegistry:
    def __init__(self, providers: list[MediaProvider], *, config: Any = None) -> None:
        self._providers = list(providers)
        self._config = config

    def providers(self) -> list[MediaProvider]:
        return list(self._providers)

    def get(self, name: str) -> MediaProvider | None:
        name = (name or "").strip().lower()
        for p in self._providers:
            if p.id == name:
                return p
        return None

    def available(self) -> list[MediaProvider]:
        return [p for p in self._providers if p.available()]

    def known_ids(self) -> list[str]:
        return [p.id for p in self._providers]

    def _setting(self) -> str:
        try:
            return str(getattr(self._config, "media_provider", "") or "auto").strip().lower()
        except Exception:  # noqa: BLE001
            return "auto"

    def choose(self, name: str = "") -> MediaProvider:
        """The provider for this call. Raises :class:`MediaError`
        (``unknown_provider`` / ``not_configured``) with plain words."""
        wanted = (name or "").strip().lower() or ""
        source = "the call"
        if not wanted:
            setting = self._setting()
            if setting and setting != "auto":
                wanted, source = setting, "the Media provider setting"
        if wanted:
            provider = self.get(wanted)
            if provider is None:
                known = ", ".join(self.known_ids()) or "none"
                raise MediaError(
                    "unknown_provider",
                    f"'{wanted}' (from {source}) is not a media service I know — the known ones are: {known}",
                )
            if not provider.available():
                hint = CONNECT_HINTS.get(provider.id, "connect it under Settings → Connections")
                raise MediaError(
                    "not_configured", f"{provider.label} is not connected — {hint}"
                )
            return provider
        ready = self.available()
        if len(ready) == 1:
            return ready[0]
        if not ready:
            hints = "; ".join(
                f"{p.label}: {CONNECT_HINTS.get(p.id, 'connect it under Settings → Connections')}"
                for p in self._providers
                if p.id != "fake"
            )
            raise MediaError(
                "not_configured",
                "No media service is connected. " + (hints or "Connect one under Settings → Connections") + ".",
            )
        names = ", ".join(p.id for p in ready)
        raise MediaError(
            "unknown_provider",
            f"More than one media service is connected ({names}) — pick one in Settings → "
            "Media provider, or name it in the call (provider=…)",
        )


def build_registry(platform: Any) -> MediaRegistry:
    """The production registry over the platform's secrets and connections."""
    from .fake import FakeProvider, fake_enabled_by_env
    from .openai_images import OpenAIImagesProvider
    from .pixio import PixioProvider

    config = getattr(platform, "config", None)
    secrets = getattr(platform, "secrets", None)
    connections = getattr(platform, "connections", None)

    def _pixio_key() -> str | None:
        key = None
        if secrets is not None:
            try:
                key = secrets.get("pixio")
            except Exception:  # noqa: BLE001 — the vault refusing is "not connected"
                key = None
        return key or os.environ.get("PIXIO_API_KEY") or None

    def _openai_key() -> str | None:
        if connections is not None:
            try:
                key = connections.credential("openai")
                if key:
                    return key
            except Exception:  # noqa: BLE001
                pass
        return os.environ.get("OPENAI_API_KEY") or None

    providers: list[MediaProvider] = [
        PixioProvider(key_resolver=_pixio_key, config=config),
        OpenAIImagesProvider(key_resolver=_openai_key),
    ]
    if fake_enabled_by_env():
        providers.append(FakeProvider(enabled=True))
    return MediaRegistry(providers, config=config)

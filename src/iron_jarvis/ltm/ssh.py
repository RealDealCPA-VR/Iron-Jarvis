"""Remote long-term-memory over SSH/SFTP (§21 extension).

A directory of markdown notes on a REMOTE host, reachable over SSH — so a user's
"second brain" can live on a server/NAS and still be searched + appended to. Same
uniform hit shape as the local markdown connector, but files are listed / read /
written over an SFTP channel.

The SFTP client is INJECTABLE (``sftp_factory``) so tests run fully offline;
production lazily builds a ``paramiko`` client (auth by a password resolved from
the vault, or a private-key file). paramiko is imported lazily so this module
imports cleanly even where it isn't installed.
"""

from __future__ import annotations

import posixpath
from typing import Any, Callable

from .base import LTMConnector, MarkdownDirConnector, _snippet, slugify


class SSHBrainConnector(LTMConnector):
    """Search / append a remote folder of ``.md`` notes over SSH (SFTP)."""

    name = "ssh"

    def __init__(
        self,
        host: str,
        remote_path: str,
        *,
        port: int = 22,
        username: str = "",
        password_resolver: Callable[[], str | None] | None = None,
        key_path: str = "",
        sftp_factory: Callable[[], Any] | None = None,
        timeout: int = 10,
    ) -> None:
        self.host = host
        self.remote_path = remote_path or "."
        self.port = int(port or 22)
        self.username = username
        self._password_resolver = password_resolver or (lambda: None)
        self.key_path = key_path
        self._sftp_factory = sftp_factory
        self._timeout = timeout

    # -- connection -------------------------------------------------------
    def _connect(self) -> tuple[Callable[[], None], Any]:
        """Return ``(closer, sftp)``; ``closer()`` tears the connection down.

        A ``sftp_factory`` (tests) short-circuits the network entirely. Otherwise
        a paramiko SSHClient connects with a private key (``key_path``) or the
        vault-resolved password, and opens an SFTP channel.
        """
        if self._sftp_factory is not None:
            sftp = self._sftp_factory()
            return (getattr(sftp, "close", lambda: None), sftp)

        import paramiko  # lazy: only when a real remote source is used

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs: dict[str, Any] = {
            "hostname": self.host,
            "port": self.port,
            "username": self.username or None,
            "timeout": self._timeout,
            "allow_agent": False,
            "look_for_keys": False,
        }
        if self.key_path:
            kwargs["key_filename"] = self.key_path
        else:
            kwargs["password"] = self._password_resolver()
        try:
            client.connect(**kwargs)
        except Exception as exc:  # noqa: BLE001 — surface as a clean, actionable error
            raise ValueError(
                f"could not connect to {self.host}:{self.port} — "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        return (client.close, client.open_sftp())

    def _md_files(self, sftp: Any) -> list[str]:
        try:
            names = sftp.listdir(self.remote_path)
        except Exception:  # noqa: BLE001 — a missing/denied dir yields no notes
            return []
        return [n for n in sorted(names) if str(n).endswith(".md")]

    @staticmethod
    def _read(sftp: Any, path: str) -> str:
        try:
            with sftp.open(path, "r") as fh:
                data = fh.read()
        except Exception:  # noqa: BLE001
            return ""
        return data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data)

    # -- LTMConnector -----------------------------------------------------
    def search(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        closer, sftp = self._connect()
        try:
            scored: list[tuple[float, dict[str, Any]]] = []
            for name in self._md_files(sftp):
                path = posixpath.join(self.remote_path, name)
                text = self._read(sftp, path)
                title = name[:-3] if name.endswith(".md") else name
                score = MarkdownDirConnector._lexical_score(title, text, query)
                if score <= 0.0:
                    continue
                scored.append(
                    (
                        score,
                        {
                            "title": title,
                            "snippet": _snippet(text, query),
                            "ref": f"{self.host}:{path}",
                            "source": self.name,
                        },
                    )
                )
            scored.sort(key=lambda item: item[0], reverse=True)
            return [hit for _, hit in scored[:k]]
        finally:
            try:
                closer()
            except Exception:  # noqa: BLE001
                pass

    def append(self, title: str, content: str) -> str:
        closer, sftp = self._connect()
        try:
            path = posixpath.join(self.remote_path, f"{slugify(title)}.md")
            existing = self._read(sftp, path).rstrip()
            body = (
                f"{existing}\n\n{content.rstrip()}\n"
                if existing
                else f"# {title}\n\n{content.rstrip()}\n"
            )
            with sftp.open(path, "w") as fh:
                fh.write(body)
            return f"{self.host}:{path}"
        finally:
            try:
                closer()
            except Exception:  # noqa: BLE001
                pass

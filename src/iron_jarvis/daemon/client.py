"""Thin HTTP client for talking to a running daemon (used by the CLI)."""

from __future__ import annotations

from typing import Any

import httpx


class DaemonClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8787") -> None:
        self.base_url = base_url.rstrip("/")

    def health(self) -> dict[str, Any]:
        return httpx.get(f"{self.base_url}/health", timeout=5).json()

    def create_session(
        self, task: str, agent_type: str = "builder", provider: str | None = None
    ) -> dict[str, Any]:
        return httpx.post(
            f"{self.base_url}/sessions",
            json={"task": task, "agent_type": agent_type, "provider": provider},
            timeout=120,
        ).json()

    def sessions(self) -> dict[str, Any]:
        return httpx.get(f"{self.base_url}/sessions", timeout=10).json()

    def cancel(self, session_id: str) -> dict[str, Any]:
        return httpx.post(
            f"{self.base_url}/sessions/{session_id}/cancel", timeout=10
        ).json()

    def rerun(self, session_id: str) -> dict[str, Any]:
        return httpx.post(
            f"{self.base_url}/sessions/{session_id}/rerun", timeout=120
        ).json()

    def delete(self, session_id: str) -> dict[str, Any]:
        return httpx.delete(
            f"{self.base_url}/sessions/{session_id}", timeout=10
        ).json()

"""Shared Secrets Vault (§7, §10).

A single, Fernet-encrypted-at-rest store for every credential the platform needs
to talk to models and integrations: provider API keys, OAuth logins, and
communication tokens. Values are encrypted on write and **never** returned or
logged except through the explicit, server-side ``get``/``get_oauth`` calls;
listing and the agent-facing tools expose metadata only (name/kind), never the
secret itself.
"""

from __future__ import annotations

from .manager import SecretsManager
from .models import SecretRecord
from .tools import SecretListTool, SecretSetTool, secret_tools

__all__ = [
    "SecretsManager",
    "SecretRecord",
    "SecretListTool",
    "SecretSetTool",
    "secret_tools",
]

#!/usr/bin/env python3
"""Load gitignored .env keys into the encrypted Epic/Iron Jarvis secrets vault.

Usage (from repo root):
  uv run python scripts/load_env_to_vault.py

- Never prints secret values.
- Never writes secrets into tracked source files.
- Requires a local .env (gitignored) or already-exported env vars.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Map env var → vault secret name used by Connections / integrations.
ENV_TO_VAULT: list[tuple[str, str]] = [
    ("XAI_API_KEY", "xai_api_key"),
    ("GROQ_API_KEY", "groq_api_key"),
    ("GROQ_API_KEY_ALT", "groq_api_key_alt"),
    ("HUGGINGFACE_API_KEY", "huggingface_api_key"),
    ("TAVILY_API_KEY", "tavily_api_key"),
    ("FIRECRAWL_API_KEY", "firecrawl_api_key"),
    ("FAL_KEY", "fal_key"),
    ("PIXIO_API_KEY", "pixio_api_key"),
    ("UPLOADTHING_SECRET", "uploadthing_secret"),
    ("UPLOADTHING_APP_ID", "uploadthing_app_id"),
    ("RAILWAY_API_TOKEN", "railway_api_token"),
    ("RAILWAY_TOKEN", "railway_token"),
    ("CLOUDFLARE_ACCOUNT_ID", "cloudflare_account_id"),
    ("CLOUDFLARE_API_TOKEN", "cloudflare_api_token"),
    ("ANTHROPIC_API_KEY", "anthropic_api_key"),
    ("OPENAI_API_KEY", "openai_api_key"),
    ("OPENROUTER_API_KEY", "openrouter_api_key"),
    ("STRIPE_SECRET_KEY", "stripe_secret_key"),
    ("STRIPE_WEBHOOK_SECRET", "stripe_webhook_secret"),
    ("TELEGRAM_BOT_TOKEN", "telegram_bot_token"),
]


def _load_dotenv(path: Path) -> int:
    """Load KEY=VALUE lines into os.environ without overriding existing vars."""
    if not path.is_file():
        return 0
    n = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if not key:
            continue
        if key not in os.environ or not os.environ.get(key):
            os.environ[key] = val
            n += 1
    return n


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    env_path = root / ".env"
    loaded = _load_dotenv(env_path)
    print(f"dotenv: loaded {loaded} unset vars from {env_path.name if env_path.is_file() else '(missing)'}")

    # Build platform so SecretsManager + DB exist under project .ironjarvis/
    sys.path.insert(0, str(root / "src"))
    from iron_jarvis.platform import build_platform

    platform = build_platform(str(root))
    secrets = platform.secrets
    stored = 0
    skipped = 0
    for env_name, vault_name in ENV_TO_VAULT:
        val = os.environ.get(env_name, "").strip()
        if not val:
            skipped += 1
            continue
        secrets.set(vault_name, val)
        stored += 1
        print(f"  vault: set {vault_name} (from {env_name}, len={len(val)})")

    # Mark LLM providers connected (vault + ConnectionRecord) without printing keys.
    for provider, env_name in (
        ("xai", "XAI_API_KEY"),
        ("groq", "GROQ_API_KEY"),
    ):
        key = os.environ.get(env_name, "").strip()
        if not key:
            continue
        try:
            platform.connections.set_api_key(provider, key)
            print(f"  connections: {provider} connected")
        except Exception as exc:  # noqa: BLE001
            print(f"  connections: {provider} skip ({type(exc).__name__})")

    print(f"done: stored={stored} missing_env={skipped} home={platform.config.home}")
    print("SECURITY: rotate any keys that were pasted into chat; .env is gitignored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

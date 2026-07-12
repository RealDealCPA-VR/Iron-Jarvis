# Epic Tech AI — token & secret policy

## Never hardcode secrets

API keys, bot tokens, Stripe secrets, Cloudflare tokens, Railway tokens, and
GitHub PATs **must never** appear in:

- source code
- commits / PRs
- README / docs examples with real values
- chat logs (if pasted, **rotate immediately**)

## Where secrets live

| Place | Purpose |
|-------|---------|
| `.env` (gitignored) | Local bootstrap only |
| Encrypted vault (`.ironjarvis/` / `EPIC_HOME`) | Runtime secrets via Secrets UI / `load_env_to_vault.py` |
| Process env (`XAI_API_KEY`, `GROQ_API_KEY`, …) | Optional fallback for providers |
| Stripe / TG secrets | Env or vault names in config — **values never in config.toml as plaintext keys** |

## Token consumption

| Control | Config key | Default |
|---------|------------|---------|
| Tokens per run | `max_tokens_per_run` | `0` (off) |
| Tokens per day | `max_tokens_per_day` | `0` (off) |
| USD per day (estimate) | `max_usd_per_day` | `0` (off) |
| Runs per hour | `max_runs_per_hour` | `0` (off) |
| Prefer local Ollama | `prefer_local_when_capable` | `false` |
| Billing gate | `billing_enabled` + `billing_require_credits` | off |

Set these in **Dashboard → Settings** or `config.toml`. Local `mock` / `ollama`
providers do not burn credits.

## Bootstrap after you put keys in `.env`

```powershell
uv run python scripts/load_env_to_vault.py
```

Prints secret **names** and lengths only — never values.

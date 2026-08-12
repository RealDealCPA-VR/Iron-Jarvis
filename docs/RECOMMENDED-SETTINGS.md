# Recommended Settings

*Grounded in `core/config.py` as of v1.167.2. Everything here is changeable
on the Settings page (or `config.toml`); the daemon re-arms live settings
without a restart wherever the page says so.*

## The short version (a good daily-driver profile)

| Setting | Recommended | Why |
|---|---|---|
| `default_provider` / `default_model` | your real workhorse (local endpoint or a connected CLI) | The fresh-install default is `mock`. Leaving it there means scripted answers. Pick the model you actually want answering chat. |
| `strict_model_pin` | `false` (on only if you demand exact ids) | With it on, a retired/renamed model id refuses instead of resolving. |
| `max_concurrent_sessions` | `2`–`3` | New in v1.166: caps how many agent sessions run at once; overflow parks as **Queued** honestly. `0` = unlimited (the default) is fine until a Team job fans out while a schedule fires. |
| `max_agent_steps` | `12` (default) | Raise only for genuinely long tasks; each step is a model call. |
| `git_native` | `true` for coding projects | Sessions run on a git worktree branch and finish as a **review** — never auto-merged. |
| `memory_steward_enabled` | `true` (default) | Curates memory by *proposal*, never silently. |
| `skill_learning_enabled` / `skill_learning_auto_approve` | `true` / `false` (defaults) | It suggests skills from your successful sessions; you approve. |
| `mcp_auto_approve` | `false` (default) | Approve MCP tool calls until you trust a server; flip per-server first if needed. |
| `autonomy_enabled` | `false` until you want it; then `autonomy_level: "suggest"` first | The caps (`5` actions / `50k` tokens per day) and the kill switch stay. |
| `self_dev_enabled` | `false` | Only for letting agents edit Iron Jarvis's own source (review-gated). |
| `event_retention_days` | `90` (default) | The activity ledger's history window. |
| `search_roots` | your client-documents folders | `file_search` and memory grounding can then reach the folders where real work lives. |
| `default_persona` | whichever voice you want by default | Free text or a built-in slug; per-thread override always wins. |

## Local-model settings (pair with `docs/LOCAL-MODELS.md`)

| Setting | Recommended | Why |
|---|---|---|
| `ollama_base_url` / `ollama_model` | your Ollama endpoint + daily model | The simplest local hookup. |
| `custom_base_url` / `custom_model` | your OpenAI-compatible server (LiteLLM, vLLM, LM Studio) | For anything Ollama doesn't front — e.g. a LiteLLM box exposing several models. |
| `model_context_windows` | pin every local model you use, e.g. `{"qwen3:32b": 32768}` | The context budget plans history against this. Unpinned models fall back to probe/default and may under-use or overflow. |
| `model_roles` | route steps to strengths, e.g. `{"plan": "custom:frontier", "extract": "ollama:qwen3:8b", "vision": "custom:vision", "judge": "custom:frontier"}` | Batch/decompose pipelines pick the right model per step. Empty = dormant. |
| `prefer_local_when_capable` | `true` once your local model clears the bar | Routes eligible work local **only after evidence**: |
| `local_quality_bar` / `local_quality_min_samples` | `0.75` / `3` (defaults) | A local model must average ≥ 0.75 across ≥ 3 evaluated sessions before auto-tier trusts it with that task class. |
| `decompose_local_tasks` | `true` (default) | Weak local models get plan → execute → verify instead of one long leap. |
| `decompose_all_tasks` | `false` unless you want visible plans everywhere | Extends decomposition to frontier models too. |
| `embedder_provider` / `embedder_model` | `auto` / `nomic-embed-text` | Local embeddings for knowledge/memory retrieval; pull the model in Ollama once. |
| `routing_local_ladder` | your fallback order, e.g. `["custom:frontier", "ollama:qwen3:8b"]` | Explicit order beats guessing when several local models exist. |

## Notifications & channels

Add the built-in **This PC** desktop destination first (zero setup), then
Slack (one field) or Telegram (chat-id auto-detect). Route event kinds per
destination — schedule outcomes to the phone, everything else to the desktop.
Adding a destination *is* the test; the row records the last test result.

## Things to leave alone

- `sandbox` / `sandbox_runtime`, `permissions`: the defaults are the safety
  floor (deny-floored tools stay deny-floored; `repl` stays ask).
- `fleet_sampling_*`: telemetry cadence — fine as shipped.
- `context_compaction`: the 0.70 suggest / ~0.92 auto thresholds are tuned;
  chat always asks before compacting when you're present.

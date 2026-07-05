# Iron Jarvis — "Seamless to use, flawless LLM connections" Audit

**Goal:** seamless for anyone to use; LLM connections via OAuth flawless and
clear; the kind of project no one wants to stop using.

**Method:** a swarm — a read-only auditor produced the findings below; parallel
builder agents implemented the fixes (OAuth/Connections backend, OpenAI+Google
adapters, onboarding/doctor, dashboard pass); central wiring tied it together.

**Result:** all high/medium findings fixed. **254 offline tests pass** (was
~230); the headline "connect a model" path is now real and OAuth-capable.

---

## Findings → Fixes → Proof

| # | Sev | Finding (identified) | Fix (implemented) | Proof |
|---|-----|----------------------|-------------------|-------|
| **F1** | HIGH | README said `secret-set anthropic_api_key` connects Anthropic, but availability + the adapter read **only the env var** — the vault key did nothing, so users silently got the mock model. | `ProviderManager` now takes a **credential resolver** (Connections/vault); `AnthropicAdapter` accepts the key; `available()` for anthropic/openai/google is gated on a **real credential**. The new `ironjarvis connect` / Connections page sets it. | Probe: after `set_api_key("anthropic", …)` → `providers.available("anthropic") == True`. README updated to `ironjarvis connect`. |
| **F2** | HIGH | **No OAuth flow anywhere** despite being the headline; `set_oauth` existed but nothing drove a handshake; the dashboard "oauth" option was a dead string. | New `connections/` module: a correct **OAuth 2.0 + PKCE** client (S256), `ConnectionRegistry` (start/complete/refresh), provider specs (anthropic/openai key, **google OAuth**, generic), tokens stored **only in the encrypted vault**. Daemon `/oauth/{provider}/start|callback`; dashboard **Connect with OAuth** button + popup. | `test_connections.py` (10 tests): PKCE S256 round-trip, full code-exchange happy path, vault-only secrets. Probe: `/oauth/google/start` returns a valid `code_challenge_method=S256` URL. `feat-connections.png`. |
| **F3** | HIGH | Unavailable provider **silently downgraded to mock** with zero signal — users mistook fake output for a real model. | Router now emits a clear **`provider.downgraded`** event ("requested openai, ran on mock — not connected") on fallback. | Probe: session with `provider:"openai"` (unconnected) emits `provider.downgraded` and runs on mock (visible in the event log). Dashboard badges mock output. |
| **F4** | MED | Model picker was cosmetic — `fable` had no adapter, and `model` wasn't threaded; opus ran regardless. | `model` threaded end-to-end: `SessionCreate` → `create_session` → `router.complete(model=)` → `manager.get(provider, model)` builds a model-specific adapter. **OpenAI + Google adapters** added so those providers are real. | Probe: `manager.get("anthropic","claude-haiku-4-5").model == "claude-haiku-4-5"`. `test_new_adapters.py` (9 tests). |
| **F5** | HIGH | No first-run/onboarding anywhere — empty dashboard with no guidance. | New `onboarding/` module: `doctor()` env report + a live **Getting-Started checklist** (connect → run → document → teach) + `is_first_run`. Daemon `/onboarding` + `/doctor`; dashboard **first-run welcome + checklist**; CLI `ironjarvis doctor`. | `test_onboarding.py` (5 tests); checklist `done` flips after a session. `feat-onboarding.png`. |
| **F6** | MED | Default `mock` output isn't flagged as fake. | Dashboard badges `provider==="mock"` sessions/output as "offline mock model" with a Connect CTA; the `provider.downgraded` event surfaces in the stream. | Dashboard pass. |
| **F7** | HIGH | "Connecting" a model was a raw secret name/value form. | Replaced by the **Connections gallery**: provider cards, one **Connect** button each (paste-key-and-verify or OAuth), Test + Disconnect, status pills. | `feat-connections.png`. |
| **F8** | HIGH | New Session provider was a free-text input. | Availability-aware **provider/model `<select>`** from `/models` + `/health`; "no model connected → Connect one" hint. | Dashboard pass; `/models` + `/health` drive it. |
| **F9** | MED | No global daemon-offline banner; WS vs HTTP signals could disagree. | One shared `/health` poll lifted into the layout → single dismissible **offline banner**; sidebar dot reads the same source. | Dashboard pass. |
| **F10** | MED | Empty states were dead ends. | `Empty` gained an `action` slot; CTAs added ("Connect a model →"). | Dashboard pass. |
| **F12** | MED | No toast system; confirmations easy to miss. | Lightweight toast provider for transient success/error. | Dashboard pass. |
| **F13** | LOW | No command palette / shortcuts. | Hand-rolled **⌘K command palette** (navigate, New Session, Connect a model). | Dashboard pass. |
| **F15** | MED | No one-command launch; 5+ steps across two terminals. | **`ironjarvis up`** starts the daemon + dashboard and opens the browser; **`ironjarvis doctor`** checks prereqs with fix hints. | `ironjarvis --help` shows `up`/`doctor`/`connect`; README updated. |
| **F16** | LOW | `OfflineHint` hardcoded the port. | Interpolated from `API_BASE`. | Dashboard pass. |
| **F17** | LOW | README test-count inconsistency (200+ vs 230). | Aligned to the real number (254). | README. |

*(F11 integrations secret-linking and F14 review N+1 polling were noted as
lower-priority follow-ups; the connections/secrets deep-linking pattern now
exists for F11 to adopt.)*

---

## What was added (swarm output)

- `src/iron_jarvis/connections/` — OAuth2+PKCE + ConnectionRegistry (10 tests).
- `src/iron_jarvis/providers/adapters/{openai,google}.py` — real adapters (9 tests).
- `src/iron_jarvis/onboarding/` — doctor + getting-started checklist (5 tests).
- Central wiring: ProviderManager credential resolver, `provider.downgraded`
  signal, model threading, `/connections` + `/oauth` + `/onboarding` + `/doctor`
  endpoints, `ironjarvis up` / `doctor` / `connect`.
- Dashboard: Connections gallery, first-run onboarding, provider/model picker,
  offline banner, command palette, toasts, mock badge.

## Proof summary
- `uv run pytest -q` → **254 passed, 1 skipped**.
- Live probe: connect→available (F1), OAuth PKCE URL (F2), downgrade event (F3),
  model threading (F4), `/connections` + `/oauth/start` + `/onboarding` + `/doctor`
  all respond; `ironjarvis doctor`/`up`/`connect` present.
- Dashboard: production build green; screenshots in `dashboard/proof/`
  (`feat-connections.png`, `feat-onboarding.png`).

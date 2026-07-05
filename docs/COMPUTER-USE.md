# Iron Jarvis — Computer Use (opt-in, safe by construction)

Computer Use lets agents drive a real browser (and, behind the strongest gates,
a desktop) to do work no API exposes. It is **OFF by default** and built to a
strict safety spec — every best practice below is enforced in code, not just
documented.

> ⚠️ Enable it **only** on an isolated/disposable VM or container. The daemon
> executes actions; treat it like remote code execution.

## Enable it

Dashboard → **Computer Use** → toggle on, set a **domain allowlist** and
**action allowlist**. Or:

```bash
# config (off by default): .ironjarvis project config or env
# POST /computeruse/enable {"enabled": true, "domain_allowlist": ["github.com"], "action_allowlist": ["navigate","read","extract"]}
```

A real browser needs Playwright's browsers once: `uv run playwright install chromium`.

## How each best practice is enforced (file → mechanism)

| Best practice | Where | How |
|---|---|---|
| **Prefer APIs over UI control** | `policy.py`, agent tools | Agents use real integrations/connections first; computer-use tools are the last resort, action-allowlisted. |
| **DOM/accessibility selectors over screenshots** | `base.py` `Selector`, `browser.py` | Actions target role+name / label / text / css. `PlaywrightBrowser` uses `get_by_role`/`get_by_label`/`get_by_text`. No raw coordinates in the normal path. |
| **Screenshots only as fallback** | `harness.py`, `base.py` | `screenshot_click` is **refused unless `fallback=True`**; when used it's recorded as a labelled fallback in the trace. |
| **Isolation (VM/container/disposable browser)** | `browser.py` `PlaywrightBrowser` | Launches a fresh **incognito `browser.new_context()`** per run; disposable. Deploy guide says run the daemon in a container/VM. |
| **Domain + action allowlists** | `policy.py` `ComputerUsePolicy` | `check()` denies navigation off the domain allowlist and any action not on the action allowlist. |
| **Credentials / payments / personal / destructive → explicit human approval** | `policy.py` `classify`, `harness.py`, `approvals.py` | Typing into password/payment/PII fields and destructive/transactional verbs (delete/buy/pay/send/transfer/confirm) create an **ApprovalRequest** and **block** until a human approves; fail-closed when no resolver. |
| **Treat web/email/PDF/on-screen as untrusted** | `safety.py` `wrap_untrusted` | Extracted page text is labelled untrusted **data**, never executed as instructions. |
| **Stop on suspected prompt injection / phishing** | `safety.py` `detect_injection`, `harness.py` | Every extracted text is scanned (instruction-override, credential harvest, urgency+payment phishing); a hit **stops the run** (`blocked`). |
| **Verify final state programmatically** | `base.py` `Checkpoint.verify`, `harness.py` | Each checkpoint asserts a real predicate (`url_contains`/`text_present`/`dom_has`) against the live page — the run is `completed` **only** if verifications pass. Never asks the model "are you done?". |
| **Record traces / screenshots / actions / errors / artifacts** | `trace.py` `TraceRecorder` | Every action, result, error, screenshot (saved to the ArtifactStore), and approval is recorded; `GET /computeruse/runs/{id}` returns the trace. |
| **Step budgets, retry limits, recovery** | `harness.py`, `policy.py` | `max_steps` budget (raises `BudgetExceeded`), per-step `max_retries` with a recovery path (alternate selector / re-read). |
| **Decompose into checkpoints with independent validation** | `base.py`, `harness.py` | A task = a list of `Checkpoint`s, each validated on its own; the run stops at the first unrecoverable checkpoint. |

## Surfaces
- Tools (gated by `policy.enabled`): `browse`, `web_extract`, `web_action` (perm `ask`), `computer_use_status`.
- Daemon: `GET /computeruse`, `POST /computeruse/enable`, `GET /computeruse/approvals`,
  `POST /computeruse/approvals/{id}/approve|deny`, `GET /computeruse/runs/{id}`.
- Dashboard: the **Computer Use** page (enable, allowlists, live approval queue).

## Proof
`tests/test_computeruse.py` — **15 offline tests** (FakeBrowser, no real browser),
one per best practice: disabled-by-default, allowlist-deny, approval-on-
credentials, approval-on-destructive, injection-stop, step-budget, screenshot-
fallback-only, and **programmatic-verify-fails-the-run**. Full suite **277 green**.

# Iron Jarvis — Computer Use (opt-in, safe by construction)

Computer Use lets agents drive a real browser **of the app's own** (and, behind
the strongest gates, a desktop) to do work no API exposes. It is **OFF by
default** and built to a strict safety spec — every best practice below is
enforced in code, not just documented.

This is **not** the feature that reads the tabs in the Chrome you use every day;
that is **Your browser**, and the next section tells the two apart.

> ⚠️ Enable it **only** on an isolated/disposable VM or container. The daemon
> executes actions; treat it like remote code execution.

## Two browsers, and which one this page is about

The **Browser** page in the dashboard holds two different features, and they
share no code, no profile and no cookies. Read this first or the rest of this
file will describe the wrong one.

**Your browser** is the Chrome or Edge *you* already use, logged into the sites
you are already logged into. Jarvis reaches it through a small **browser add-on**
you load yourself and then **Pair**, and it can only see a tab once you have
granted the add-on access to that site. As of **v1.237.0 it can also act on the
page**: alongside listing your tabs, naming the tab you are looking at, reading a
page and photographing it, it can click, type, scroll, press a key, open, switch
or close a tab, and navigate. Acting is a separate setting — **Interactive** —
and it is not what **Read only** grants.

**Acting in your browser asks first, in two layers.** All eight acting abilities
default to **ask**, so an ordinary click on an ordinary button already stops and
shows you an approval card; nothing reaches your browser until you say yes. On
top of that, the four that change a page — click, type, press a key, navigate —
sit on the **deny floor**, so setting them to *allow* is dropped rather than
obeyed, and even a session-wide "allow for this conversation" is overruled when
the target looks destructive or transactional, when the page marks the field
sensitive, or when the control cannot be identified well enough to judge. A
button called "Delete account" asks every time; so does a target with no
readable name.

**Downloads.** If a page Jarvis clicks starts a download, Chrome owns it and the
file lands wherever your own Chrome settings put it, normally `~/Downloads`.
Jarvis can read a completed download and copy it into a project; it cannot
silently redirect where Chrome saves it. (`rename_file` refuses to move a file
from outside the workspace — that refusal is the documented behaviour, not a
bug; the copy is `POST /documents/save-copy`.)

**A harness can use it too.** A coding CLI launched in a Build pane whose
**Browser** capability is ticked drives this same browser through Jarvis, behind
the same approvals and the same ledger, on a credential that belongs to that pane
and dies with it. Computer use is untouched by that: it has no pane capability
and no add-on.

Browser access ships **off**, and its three settings (off / read only /
interactive) are separate from everything below — turning Computer Use on does
not touch it, and turning it off does not disable Computer Use. **Your browser has
its own guide: `docs/BROWSER.md`** — loading the add-on, pairing, the three access
modes, the security model, and the sixteen limits of this first version. The
Handbook's Browser section is the short version of the same ground.

**Computer use** — the rest of this file — is the other one: a **separate,
headless Chromium** the daemon launches itself, in a fresh incognito context per
run, logged into nothing. It shares no cookies or sessions with your browser, so
it cannot reach a site you are signed into unless it signs in itself. It acts
too, and it is the half wrapped in domain allowlists, action allowlists and
human approvals of its own.

One sentence to keep them straight: **your browser is the one you are already
logged into, and Jarvis acts in it only with your approval; the Computer Use
browser is one Jarvis owns, and it knows nobody.**

## Enable it

Dashboard → **Browser** (the route is still `/computeruse`) → the **Computer
Use** sections → toggle on, set a **domain allowlist** and an **action
allowlist**. Or:

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
- Dashboard: the **Browser** page at `/computeruse` — the Your browser card on
  top (pairing and access for your own Chrome), and below it the Computer Use
  sections (enable, allowlists, live approval queue). The page was labelled
  **Computer Use** before v1.235.0.

## Proof
`tests/test_computeruse.py` — **15 offline tests** (FakeBrowser, no real browser),
one per best practice: disabled-by-default, allowlist-deny, approval-on-
credentials, approval-on-destructive, injection-stop, step-budget, screenshot-
fallback-only, and **programmatic-verify-fails-the-run**. Full suite **277 green**.

# The sidebar, and setup that takes one click — implementation plan (v1.240.0 → v1.242.0)

**Status:** binding. Written 2026-09-07, after the five-ship browser capability
shipped (v1.235.0 → v1.239.0). Grounded in four subsystem investigations of the
working tree at `1895ac5`, not in memory of how the code used to look.

---

## 0 · What the user asked for, in their words

> "is there a way to make this way easier where everything is just automated for
> the user when they decide to complement it with a few approvals?"

> "include an HTML modal popup that cleanly and beautifuly explains the bare
> minimum steps the user needs to take as well as give the user a chat rightbar
> with the extension in the brower of their choosing so we function exactly like
> the perplexity brower assistant acts, whereby the user interacts with the
> sidebar jarvis version in the capacity it has over the browsers control and
> allows the user to stop any agentic function at any time or stear it"

Four deliverables, each checked against a named acceptance behaviour in §7:

1. Setup automated down to the approvals Chrome will not let us skip.
2. A guided modal that explains the bare minimum, beautifully.
3. A chat sidebar docked in the browser, in the shape of the Perplexity assistant.
4. Stop any agentic function at any time, and steer it.

---

## 1 · The decision this reverses, and the principle it keeps

`docs/BROWSER-PLAN.md` D28 says the add-on surface is status-only, and names
"agent chat" first in its do-not-place list, on the grounds that **Jarvis is the
product and the extension is an I/O adapter**.

**D28 is superseded for the side panel, and re-affirmed for everything else.**
The user asked for the sidebar explicitly, which is the authority to revisit a
decision they made. But the principle behind D28 survives intact, and it is what
makes the sidebar safe to build:

> The side panel holds **no model, no agent loop, no policy, and no tool
> settings**. It renders a conversation that runs entirely in the daemon, under
> the same approval gate, the same access mode, and the same risk tiers as every
> other Jarvis surface. It is a *view*, not a second Jarvis with its own rules.

The popup's do-not-place list therefore still stands as written. Model selection
and tool settings stay out of the browser. What moves in is a window onto a chat
that was always going to be governed from the daemon side.

**D32 [NEW].** The action click opens the side panel, not a popup. Chrome ignores
`openPanelOnActionClick` when `action.default_popup` is set, so the popup is
retired and its status readout becomes the panel header. Keeping both would cost
a click forever and split the status story across two surfaces.

**D33 [NEW].** The panel talks to the daemon over the **socket it already has**.
No new credential, no new origin exception, no HTTP from the add-on. See §3.

**D34 [NEW].** Stop is **addressable and out-of-band**, not connection-bound.
See §4 — this is the finding that splits the work into three ships.

---

## 2 · The four findings that shaped this plan

Each is a fact from the working tree, with the file that proves it.

**F1 — The add-on cannot ask the daemon a question.** The protocol is
deliberately asymmetric: the daemon mints `req_` ids and owns the only pending
future map; the extension answers commands and fires three unsolicited
notification kinds. `protocol.py:176-179` states the intent outright — the
separate `evt_` prefix exists *so that* an extension-minted id can never resolve
a command future. An unknown inbound type on a paired socket is recorded as a
fault and dropped (`extension_backend.py:1223-1227`).

*Consequence:* the sidebar needs a new frame pair. There is no workaround, and
the HTTP alternative is worse (see F2).

**F2 — The pairing token is refused everywhere except `/browser/ws`.** It is
checked only by `browser_ws_token_ok` (`daemon/auth.py:133-181`), which never
consults `IRONJARVIS_TOKEN`. Chat routes take the install bearer.

*Consequence:* an HTTP channel from the add-on would need the add-on to hold the
install bearer, or the daemon to call itself. Both are credential substitution,
which this codebase has five standing rules against. Rejected. The socket the
add-on already holds is the right channel, and it is already authenticated.

**F3 — `/chat/stream` cancellation is connection-bound.** There is no run id and
no cancel route. The only cooperative check is `request.is_disconnected()`, once
per tool round (`routes/chat.py:1882`); mid-generation stop works only because
Starlette cancels the generator when the HTTP client goes away
(`routes/chat.py:2243-2255`). A chat turn is otherwise stateless — the tool
context is literally `session_id="chat"` (`routes/chat.py:1694`).

*Consequence:* **a panel turn has no HTTP connection to drop.** The daemon runs
it on the panel's behalf, so the entire existing stop mechanism is unavailable.
Stop must become addressable first. This is Ship 2, and it is not optional
scaffolding — without it the user's "stop any agentic function at any time" is
undeliverable for the sidebar.

**F4 — A chat-lane approval is announced only in the SSE frame.** It is
deliberately excluded from `GET /chat/approvals/pending`
(`routes/chat.py:377-394`), so a client not holding the stream can never discover
it. But `POST /chat/approvals/{id}` answers chat, agent-runtime and MCP asks
alike (`routes/chat.py:339-361`), over one process-local registry
(`core/approvals.py`).

*Consequence:* the panel can reuse the approval registry untouched. It must carry
the ask down its own socket, exactly as the SSE lane carries it down the wire,
and answer through the same `resolve()`.

---

## 3 · The protocol addition (D33)

Two new frame kinds, one per direction, added to `browser/protocol.py` — the
single source of truth that generates `extensions/chrome/src/protocol.ts` and is
byte-compared by `tests/test_browser_protocol_v1235.py`.

```text
EXTENSION -> DAEMON    browser.panel        {type, action, params}
DAEMON -> EXTENSION    browser.panel_event  {type, event, payload}
```

This fits the existing grain rather than fighting it. `browser.event` is already
fire-and-forget upward; `browser.directive` is already unsolicited downward. The
panel is an **event-driven view, not an RPC client**, so it needs no
request/response correlation and no second pending map — which is precisely the
asymmetry F1 says the protocol was designed to preserve.

- `action` is one of: `open` · `send` · `stop` · `steer` · `approve` · `deny` · `close`
- `event` is one of: `state` · `delta` · `tool` · `approval` · `steered` · `done` · `error`

**Gating.** `browser.panel` is refused on an unpaired socket (it is not added to
`RESTRICTED_INBOUND_FRAMES`) and refused entirely when `browser_access` is `off`.
The tools a panel turn may use follow the access mode exactly: `read_only` gets
the inspection tools, `interactive` gets the full set with risk tiers intact. A
panel turn never widens what the browser may do — it is the same capability,
reached from a different window.

---

## 4 · Making a chat turn addressable (Ship 2)

`run_chat_turn` was extracted verbatim from the route in v1.136.0 precisely so
non-dashboard callers could run the same engine. Ship 2 does the same thing for
the streaming twin, for the same reason.

- Extract the prep (`routes/chat.py:1236-1831`, 596 lines) and the generator
  (`:1832-2402`, 571 lines) into a module-level async generator in
  `daemon/chat_stream.py`, mirroring `run_chat_turn`'s signature.
- Replace the single `request.is_disconnected()` call with an injected
  `should_stop` predicate. The HTTP route passes `request.is_disconnected`, so
  its behaviour is unchanged and byte-compatible.
- Add a `TurnRegistry` keyed by `turn_id`, holding the `asyncio.Task`. Stop
  cancels the task, which lands in the generator's existing `BaseException`
  handler — the same path a disconnect already takes, so the CANCELLED usage row
  and the ledger row are written by code that is already proven.
- Add `POST /chat/turns/{turn_id}/stop`, which the dashboard gains for free.

**This is a refactor, and it is the riskiest hour of the three ships**, because it
moves the most important surface in the app. It ships alone, with the whole
existing chat suite as its proof, and nothing else in the same version.

---

## 5 · Steering, honestly

There is no route anywhere in Iron Jarvis that injects a message into a running
turn. Three things get called "steer" and only the first two exist today:

1. **Deny an approval.** Already real. The model is told the user declined
   (`routes/chat.py:2110`), and the refusal is ledgered as a human decision.
2. **Stop.** Real after Ship 2.
3. **Redirect mid-turn.** New. A `steer` action queues a note injected as a user
   message at the **next tool-round boundary** — the one place the loop is
   already re-entrant and already checks for stop. It cannot interrupt a
   half-generated sentence, and the panel says so rather than pretending.

The panel shows a steer note as *pending* until the boundary consumes it, so the
user is never told their correction landed before it did.

---

## 6 · The three ships

| Ship | Version | What it delivers | Risk |
|---|---|---|---|
| 1 | v1.240.0 | One-click setup: guided modal, auto-pair window, auto-grant, access folded in | Low, additive |
| 2 | v1.241.0 | The chat turn becomes addressable: extraction, turn registry, real stop | **High, refactors chat** |
| 3 | v1.242.0 | The sidebar: panel page, frame pair, panel turns, stop/steer/approve | Medium |

Ship 1 answers the "way easier" question completely and is worth having even if
the sidebar were never built. Ship 2 buys nothing a user can see except a Stop
button that works from a second window — it exists so Ship 3 can be honest.

### Ship 1 · One-click setup (v1.240.0)

> **AUTO-PAIR WAS DESIGNED, BUILT, AND THEN REMOVED. 2026-09-07.**
>
> This section originally specified that an armed window would complete a pairing
> automatically when the connecting add-on's identity matched
> `PINNED_EXTENSION_ID`. It was built that way, and a security review then
> **demonstrated the exploit** against the real `create_app()` with the full
> middleware and a bearer token set. Three facts combine:
>
> * `/browser/ws?pairing=1` needs no credential — `TokenAuthMiddleware` is a
>   `BaseHTTPMiddleware` and cannot see WebSockets at all.
> * The identity is derived from the `Origin` header, and a non-browser client
>   writes its own headers. `HostOriginGuardMiddleware` compares that string, which
>   is a browser boundary, not a process boundary.
> * `PINNED_EXTENSION_ID` is a public constant shipped in every install.
>
> So any local process could send the pinned origin during the window and receive
> `browser.paired {token: ...}` with no human press. It would then lock the real
> add-on out permanently, because `complete_pairing` refuses a second pairing,
> while the card read **Connected**.
>
> **There is no cryptographic repair.** Any secret the real add-on can read, a
> process running as the same user can also read, so no shared secret separates
> them. The only thing that can tell a real browser from a local impostor is a
> person looking at a surface they trust.
>
> The narrowing that was considered and rejected — refusing to auto-pair when more
> than one browser is waiting — does not help: the attacker wins the race long
> before Chrome finishes loading an unpacked add-on, so it is the only candidate.
>
> **The manual Pair press is therefore permanent.** It is not friction to be
> engineered away; it is the security boundary of the whole browser capability,
> and it is faithful to the request, which asked for automation *complemented by a
> few approvals*. Pair is that approval. Everywhere else in `routes/browser.py`
> the origin-derived id is documented as authorising nothing, and that stays true.

- `POST /browser/setup/arm` opens a **time-boxed** window (120 s). It mints no
  credential and decides no identity. It exists for the two things that are safe
  without a human: sending the host-permission directive (which opens the add-on's
  own page and hands over nothing), and telling the dashboard a setup is in
  progress so the **Pair** press can be put in front of the user in context
  instead of on a card behind the dialog.
- **Arming writes no setting.** The first build had the modal post
  `{access: "read_only"}` on mount, which persisted a capability change to
  `config.toml` that pressing Close did not revert — a user who opened the dialog
  merely to read the steps came away with the browser capability switched on and
  nothing telling them. The access level is now a visible control that writes only
  on an explicit click.
- `BrowserSetupModal` on the Browser page, built on the shared `Modal` portal
  primitive (not the hand-rolled `FirstRunWizard` overlay), matching
  `EnableDialog`'s section and footer conventions, reusing `FirstRunWizard`'s
  `ArcMark` and stepper.
- Three steps, and the modal advances itself by polling `/browser/status`: open
  the folder, load it in Chrome, allow site access. Each step shows the one thing
  the user must do and nothing else.
- Access mode is set by the modal as part of arming, defaulting to Read only.
- Any new amber utility must be added to the `light-amber-overrides` block in
  `globals.css`, or `light-amber-v1233.test.tsx` goes red.

### Ship 2 · The addressable turn (v1.241.0)

As §4. Acceptance: the existing chat suite stays green with zero edits, plus a
new test proving a turn stopped from a *second connection* writes the CANCELLED
row and stops billing.

### Ship 3 · The sidebar (v1.242.0)

- `src/sidepanel/sidepanel.html` and `.ts`, in the popup's hand-written idiom:
  inline `<style>`, `:root` tokens, trailing classic `<script>`, `data-*` state
  attributes. No framework — the add-on has no bundler for one and will not grow
  one for this.
- `manifest.json`: add `"sidePanel"` to `permissions`, add `side_panel.default_path`,
  and **remove** `action.default_popup` (D32). The Chrome 120 floor already clears
  the 114 the API needs, so the support matrix does not move.
- Five places change together because the build machinery derives filenames by
  string match: `esbuild.config.mjs` `ENTRY_POINTS` and the HTML copy list,
  `scripts/build.mjs` `requiredFiles()` (which reads `action.default_popup` today
  and must learn `side_panel.default_path`), `onboarding/doctor.py`
  `BROWSER_ADDON_RUNTIME_FILES`, and `test_browser_packaging_v1239.py`.
- The panel header carries the status readout the popup used to own, so retiring
  the popup loses nothing.

---

## 7 · Acceptance matrix

Every row is a behaviour a person can drive, not a unit that can pass in
isolation. Rows 1-4 are Ship 1, row 5 is Ship 2, rows 6-10 are Ship 3.

| # | Behaviour | Proven by |
|---|---|---|
| 1 | A forged extension origin is never handed a credential, inside an armed window or out | real socket against the real `create_app()`, with an anti-vacuity guard proving the socket was offered first |
| 2 | An armed window sends the site-access directive only to the connection that is actually adopted | route test plus a mutation deleting the adoption check |
| 3 | A completed pairing shuts the window, so a later Forget cannot reuse it | route test |
| 4 | The modal names the real folder for this machine and copies it | dashboard test over `addon_dir` |
| 5 | A turn stopped from a second connection stops, and writes CANCELLED | new test, existing suite unedited |
| 6 | The action click opens the sidebar | manifest plus source pin |
| 7 | A question typed in the sidebar is answered by the daemon's chat engine | real socket, real turn |
| 8 | Stop in the sidebar halts a running turn mid-flight | drives a real turn |
| 9 | An acting tool asks in the sidebar, and Deny is ledgered as a human decision | approval registry test |
| 10 | The sidebar with access `off` runs no turn at all | fail-closed test |

---

## 8 · What this plan will not pretend

- The sidebar cannot be installed for the user. Developer mode and Load unpacked
  stay manual until there is a Web Store listing, which needs a developer account
  and a review cycle, and is therefore a decision rather than a task.
- The grant click must originate inside the add-on. Chrome requires the gesture.
- Steering cannot interrupt a half-generated sentence (§5).
- A tool already executing is not killed by a stop; its worker thread finishes and
  its write lands. Stop prevents the *next* round, not the current call. The
  sidebar must say this rather than imply an abort it cannot perform.

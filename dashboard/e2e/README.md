# End-to-end acceptance tests (calm UI redesign)

`e2e/calm-ui.spec.ts` runs the brief's browser acceptance tests in a real
browser against a scratch stack:

| Test | What it proves |
|---|---|
| **T1** | `/` is a chat: at most 4 sidebar items, no dashboard / tiles / module grid, the composer focused on load |
| **T2** | every surface in `lib/surfaces.ts` is reached through the command palette **and** within 2 clicks from home |
| **T3** | a fresh, empty home opens to chat with no blocking modal and the mocked model answers a message |
| 390 px | `/`, `/settings` and `/everything` never pan sideways |

`e2e/ij-harness.spec.ts` (v1.369.0, IJ harness plan Phase 6) drives Build's
IJ harness view the same way:

| Test | What it proves |
|---|---|
| Session | Build → IJ harness → New session in a scratch folder → a reply whose one step writes `RESULT.md` (the demo model's scripted step) → Undo removes the file from disk |
| Rewind (v1.370.0) | on the demo model: a second message writes `RESULT.md`; **Rewind to here** first says what goes back (the file is still there), then removes it, puts the message back in the box and drops the reply; the demo model leaves no library suggestion |
| Terminal | the session's Terminal tab opens a normal Build terminal; the left pane's Terminals group opens it in the Terminals view, focused, and IJ harness comes back to the same session |
| Account limit | the Phase 5 account routes STUBBED in the browser (`page.route`, Phase 5's own shapes): the limit card's words, Resume when ready surviving a reload, Cancel, and the daemon's resume (the page rejoins the resumed turn and the card goes) |
| 390 px | the IJ harness and Terminals views never pan sideways; (v1.373.0) the `/` and `@` lists open above the box, inside the window, and Escape closes them without sending |
| `/` and `@` (v1.373.0) | on the demo model, a folder skill picked with `/` and a file picked with `@` ride a real turn, and the question the daemon saves names both |

`e2e/chat-waiting-where-you-are.spec.ts` (readiness 12B, G05) parks a chat
on its approval card (Permissions "Ask first" + the demo model's `write_file`)
with `window.Notification` recorded: the daemon's preflight allows the
`X-IJ-Viewer` header and the browser sends the stream's preflight once, not
per turn; on the chat page the card is the only notice, on another page of the
same window the bell lists it and pings once, a `/` ⇄ `/chat` remount with a
held (2.5 s) rejoin and a reload never ping, and a second window showing the
same chat keeps every bell quiet until it closes.

`e2e/model-menu-fit.spec.ts` (the model menu fits the window, 2026-10-24) opens
the composer's model menu with a `/models` answer as tall as the user's (15
providers, stubbed in the browser) from a new chat, a chat with messages and
the IJ harness (an empty session and one with a reply), at 1406 × 857,
1280 × 720, 1024 × 640 and 390 × 844: the menu's box is inside the window, the
browser hits it at its top and bottom edges (no parent clips it), the filter
box is on screen and takes the focus, the last row is reachable, nothing pans
sideways, and (the chat) a long provider's side panel fits too — on a phone
under its row, inside what the list shows. Every check is soft, so one run
reports every case; `IJ_MENU_FIT_OUT=<folder>` also writes each case's
measurements (JSON) and a screenshot. Its review cases (1406 × 857): the menu
follows its chip when the demo strip goes away, an open provider panel
follows the menu when the composer moves, an ancestor with a `transform` /
`backdrop-filter` cannot move the menu (the portal), the app's palette covers
an open menu, and the IJ harness's Ctrl+K from a row of the menu stays in it.

`e2e/composer-one-row-v1400.spec.ts` (v1.400.0, the composer's toolbar is
one row whatever model is picked) stubs `/models` with a short-named model,
the user's "Qwen3.8-27B (RTX)" with reasoning levels, two longer names and one
of 50 characters, and measures the toolbar of a new chat, a saved chat and the
IJ harness (one Build session per model) at 2000 × 1103, 1406 × 857,
1280 × 720, 1024 × 640 and 390 × 844: never a sideways pan, every control
inside the card and none over another; from a 640 px composer up, one
vertical centre, Send right-most and at least six characters of the model's
name, and once the toolbar has given anything up, no gap wider than about one
chip between its two groups. Its other cases: the user's own case on a light
theme (at 2000 × 1103 the WHOLE name "Qwen3.8-27B (RTX)" with only Web's word
given up; at 1024 × 640 the permission level and "Local" keep their words),
typing measuring nothing and leaving the row still, every toolbar menu opening
in place inside the window while the chips are compacted, the ORDER (the chips
give up their words one at a time — Web, Auto tools, "Local only" to "Local" —
and only then the model's name truncates, then the permission level), a short
name picked after a long one getting its own width back, the hysteresis (a few
pixels of jitter never flickers the chips' words), the user's case at 125 % and
150 % zoom, the IJ harness chip naming an Iron-Proxy account, and typing in
the IJ harness's box measuring nothing either.
`IJ_ROW_OUT=<folder>` writes each case's measurements and a screenshot.

`e2e/model-team-fit.spec.ts` (v1.394.0 review) opens Settings › Model team
with GET /team, /build/accounts and /team/starter stubbed to members and
accounts with long names, at 390 × 844 and 1280 × 720: every part of a
member's row (model, role boxes, account picker) and of a starter row sits
inside its own box and the page never pans sideways; the role boxes and the
account picker are reached with Tab alone.

Run from `dashboard/`:

```bash
pnpm e2e                                   # or: npx playwright test
IJ_E2E_SHOTS=../docs/redesign/screens/after pnpm e2e   # also write the evidence screenshots
npx playwright test e2e/ij-harness.spec.ts             # just the IJ harness view
```

Playwright starts both servers itself:

- a daemon on **8807** with a FRESH home (`e2e/.home`, wiped each run by
  `e2e/fresh-home.mjs`) — its default model is the offline mock;
- a Next dev server on **8808**, building into `.next-e2e` (so it never
  collides with an everyday dev server's `.next`).

`IJ_E2E_DAEMON_PORT` / `IJ_E2E_WEB_PORT` move both (a second run beside one
already holding 8807/8808); the specs that call the daemon read the same
variable.

It never touches the live app (8787/8788). It uses the Edge installed on
this PC (`channel: "msedge"`, no browser download); set `IJ_E2E_CHANNEL`
to use another installed channel.

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

It never touches the live app (8787/8788). It uses the Edge installed on
this PC (`channel: "msedge"`, no browser download); set `IJ_E2E_CHANNEL`
to use another installed channel.

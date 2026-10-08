# End-to-end acceptance tests (calm UI redesign)

`e2e/calm-ui.spec.ts` runs the brief's browser acceptance tests in a real
browser against a scratch stack:

| Test | What it proves |
|---|---|
| **T1** | `/` is a chat: at most 4 sidebar items, no dashboard / tiles / module grid, the composer focused on load |
| **T2** | every surface in `lib/surfaces.ts` is reached through the command palette **and** within 2 clicks from home |
| **T3** | a fresh, empty home opens to chat with no blocking modal and the mocked model answers a message |
| 390 px | `/`, `/settings` and `/everything` never pan sideways |

Run from `dashboard/`:

```bash
pnpm e2e                                   # or: npx playwright test
IJ_E2E_SHOTS=../docs/redesign/screens/after pnpm e2e   # also write the evidence screenshots
```

Playwright starts both servers itself:

- a daemon on **8807** with a FRESH home (`e2e/.home`, wiped each run by
  `e2e/fresh-home.mjs`) — its default model is the offline mock;
- a Next dev server on **8808**, building into `.next-e2e` (so it never
  collides with an everyday dev server's `.next`).

It never touches the live app (8787/8788). It uses the Edge installed on
this PC (`channel: "msedge"`, no browser download); set `IJ_E2E_CHANNEL`
to use another installed channel.

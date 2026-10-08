# APPROVED: decisions on AUDIT.md §9

| | |
|---|---|
| Decision by | VR, in the Claude Code session of 2026-10-08 |
| What VR said | **"yes proceed"**, in reply to the Phase 1 summary that listed the recommended answers to the open questions |
| How this file was written | Recorded by Claude from that reply. Every question below is decided as AUDIT §9 recommends. Where the audit offered no single recommendation, the interpretation is stated and marked *(interpretation)*, so VR can overturn it. |

## Decisions

| # | Question (AUDIT §9) | Decision |
|---|---|---|
| Q1 | Retire the Overview dashboard and the 30-tile grid; home = chat; operational content → Everything › Status | **Approved** |
| Q2 | Retire the Simple/Advanced switch, the seven hubs, the tab row and the short Settings view | **Approved** |
| Q3 | One model selector (in the composer); remove the title-bar model chip; "Make this my default" lives in the selector's menu | **Approved** |
| Q4a | Session board becomes a Board view of Sessions | **Approved** |
| Q4b | You + Train become Settings › Memory & you | **Approved** |
| Q4c | Connections + Directory + Secrets + Notifications + Browser become Settings › Connections | **Approved** |
| Q4d | Tools splits into Permissions and Apps & tools | **Approved** |
| Q4e | Workflows, Schedules, Reflexes and Sentinels as one list | **Deferred**: they stay separate pages under Everything › Automations |
| Q5 | Chat's thread rail merges into the app sidebar (layout only; thread behaviour unchanged); persona selector moves to the composer "+" menu | **Approved** |
| Q6 | Device-only settings join the one schema with `scope: device`, stored by the daemon per device, so chat can set them | **Approved** |
| Q7 | First run: a **one-tap suggestion** of a detected model, never silent auto-selection (keeps the never-auto-switch rule) | **Approved** |
| Q8 | Old routes of relocated pages **redirect** to their new anchor; every test path update is listed in REPORT.md | **Approved** |
| Q9 | A pasted secret in the composer is held, and the credential card is offered | **Approved** |
| Q10 | Per-key permission tiers as proposed in AUDIT §6.2 | **Approved as proposed** |
| Q11 | Pins: up to 3 user-chosen pinned entries under the 4 nav items. *(Interpretation: pins are the user's own additions and none exist by default, so the brief's "≤ 4 top-level items" is measured on a fresh profile, which T1 does. Pins do not count toward the cap.)* | **Approved** |
| Q12 | Lint: add `ruff check` (minimal rule set) and `next lint` (default Next config), enforced on files the redesign touches | **Approved** |
| Q13 | Add Playwright for T1–T3, against a scratch daemon with the model pinned to "mock" | **Approved** |
| Q14 | Fix the `comm_trust` save bug now, on master, as a patch release | **Approved** |
| Q15 | Durable writers for `permissions` and `computer_use` (in S2) | **Approved** |
| Q16 | The directory page is named **Everything** | **Approved** |
| Q17 | Help moves to the sidebar footer menu (not a 5th nav item) | **Approved** |
| Q18 | If Q2 is approved, the tests pinning the retired v1.318.0–v1.319.0 Simple surfaces may be removed (`home-simple-v1318`, `settings-simple-v1319`, and the Simple-mode blocks in `ux-wave1-shell`, `popout-v1283`, `onboarding-welcome-v1197`). Each is replaced by T1/T2 coverage and listed in REPORT.md | **Approved** |

## Standing constraints (unchanged)

- No capability is removed beyond what is approved above. The approved items
  are relocations and retirements of *navigation shapes*, not features.
- Chat and Build behaviour is unchanged (restyle only).
- Work happens on `redesign/calm-ui`, with one commit per plan step.
- Secrets never appear in chat text, logs or the ledger.

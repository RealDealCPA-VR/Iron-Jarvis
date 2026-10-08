# Settings (`/settings`)

**Today** (v1.320.0), the Settings page holds 32 config fields in 4 sections
(Simple mode shows 3 of them). Other settings live on other pages:
- **Connections:** accounts and keys, Iron-Proxy.
- **Tools:** per-tool approval.
- **Autonomy:** caps, grants, kill switch.
- **Notifications:** channels.
- **Memory:** profile share.
- **Elsewhere:** the theme, the Simple/Advanced switch and the chat approval
  default are browser-only values.

**Proposed:** one Settings page with grouped sections, generated from ONE
settings schema. That same schema generates the chat config tools (AUDIT
§4.4, §6). Pages that manage *records* (workflows, schedules, reflexes,
projects…) stay as their own pages and are linked from the matching section.

## Desktop

```
┌───────────────────────┬──────────────────────────────────────────────────────┐
│ Settings              │ Models                                               │
│ [🔍 Find a setting…]  │ Which AI answers, and how.                           │
│                       │                                                      │
│ ▸ Models              │ Default model      [ Claude Opus 4.8          ▾ ]    │
│   Connections         │                    Signed in with Claude Code.       │
│   Agents & automation │ Reasoning          [ Default ▾ ]                     │
│   Memory & you        │ If a local model   [ Say so and stop ▾ ]             │
│   Permissions & ledger│   answers with an error                              │
│   Appearance          │                                                      │
│   System              │ ▸ Local and custom models (4)            show        │
│                       │ ▸ Context windows                        show        │
│                       │                                                      │
│                       │ ── Changed here or in chat ────────────────────────  │
│                       │ Default model  Opus 4.7 → Opus 4.8  · 2 min ago  [Undo]
│                       │                                                      │
│                       │                        [ Reset ]  ( Save changes )   │
└───────────────────────┴──────────────────────────────────────────────────────┘
```

- **Left:** the 7 groups (AUDIT §4.4) and a search box that filters every
  setting by its label and its plain-word aliases. The palette offers the
  same search.
- **Right:** one group at a time. Advanced keys sit in a folded "▸ …" row
  inside their own group; there is no app-wide Advanced switch.
- **"Changed here or in chat":** the ledger rows for this group's settings,
  each with Undo. These are the same rows the chat confirmation card shows
  (see `config-card.md`).
- **One primary action:** Save changes, which appears only while something is
  unsaved (today's sticky Save bar).
- **Credentials** (an API key, a bot token) open the secure credential card
  (see `credential-card.md`). They are never a plain text field that echoes
  the value back.

## Phone (390 px)

```
┌──────────────────────────────────┐
│ ←  Settings                      │
│ [🔍 Find a setting…]             │
│  Models                       ›  │
│  Connections                  ›  │
│  Agents & automation          ›  │
│  Memory & you                 ›  │
│  Permissions & ledger         ›  │
│  Appearance                   ›  │
│  System                       ›  │
└──────────────────────────────────┘
        tap "Models" ↓
┌──────────────────────────────────┐
│ ←  Models                        │
│ Default model                    │
│ [ Claude Opus 4.8            ▾ ] │
│ Reasoning                        │
│ [ Default                    ▾ ] │
│ ▸ Local and custom models (4)    │
│ ( Save changes )   ← when dirty  │
└──────────────────────────────────┘
```

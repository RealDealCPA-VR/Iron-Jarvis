# Inline config cards (in the chat transcript)

These cards appear when a chat config tool (`config_set`, AUDIT §4.4 and §6)
proposes or makes a change. They follow today's approval-card pattern
(`components/chat/ApprovalCard.tsx`) and the TurnReceipt's undo
(`onUndo`). One card is one change.

## 1. Ask first (a gated or ambiguous change)

This is shown when the setting's tier is **ask** (AUDIT §6), or when the
request is destructive or ambiguous.

```
 ┌────────────────────────────────────────────────────────────────┐
 │ ⚙ Change a setting?                                            │
 │                                                                │
 │   Default model                                                │
 │   Claude Opus 4.7  →  Qwen 3 Coder (local, this PC)            │
 │                                                                │
 │   Applies to new chats. Nothing leaves this PC with this model.│
 │                                                                │
 │   [ Not now ]                                     ( Change it ) │
 └────────────────────────────────────────────────────────────────┘
```

## 2. Done (after the change)

The setting was changed in chat. A ledger row was written with the old and new
values.

```
 ┌────────────────────────────────────────────────────────────────┐
 │ ✓ Setting changed                                    just now  │
 │   Telegram notifications   Off  →  On                          │
 │   [ Open in Settings ]                              [ Undo ]   │
 └────────────────────────────────────────────────────────────────┘
```

After Undo is pressed:

```
 ┌────────────────────────────────────────────────────────────────┐
 │ ↶ Undone — Telegram notifications is Off again                  │
 └────────────────────────────────────────────────────────────────┘
```

## 3. A schedule (a "schedule"-type change)

Asked as: "run this workflow every weekday at 7am".

```
 ┌────────────────────────────────────────────────────────────────┐
 │ ⏰ New schedule                                                 │
 │   Morning inbox triage — weekdays at 7:00 AM                    │
 │   Next run: tomorrow 7:00 AM · reports to: this chat            │
 │   [ Edit ]   [ Undo ]                                           │
 └────────────────────────────────────────────────────────────────┘
```

## 4. One clarifying question (ambiguous)

Asked as: "use the local Qwen model for coding tasks".

```
 ┌────────────────────────────────────────────────────────────────┐
 │ Which do you mean?                                              │
 │   ( ) Make Qwen the default for every chat                      │
 │   ( ) Use Qwen only in Build (coding panes)                     │
 │   ( ) Use Qwen for agent runs of the Builder                    │
 │                                                   ( Continue )  │
 └────────────────────────────────────────────────────────────────┘
```

Rules:
- Card wording uses the setting's **label**, never the raw key. The key is
  in the card's hover title and in the ledger row.
- Old → new is shown in **plain values** ("Off → On", the model's name).
- A **secret** value is never shown: the card says "API key: set" or
  "replaced" (see `credential-card.md`).

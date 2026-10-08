# Secure credential card

This card appears when a request needs a credential: "connect my Notion",
"turn on Telegram notifications", or "use my OpenAI key". The agent never
asks for the secret in the conversation. It shows this card instead.

```
 ┌────────────────────────────────────────────────────────────────┐
 │ 🔒 Connect Notion                                               │
 │                                                                │
 │   Paste your Notion integration token. It is stored encrypted  │
 │   on this PC and never shown in the chat.                      │
 │                                                                │
 │   Token  [ •••••••••••••••••••••••••••••••          ] [ 👁 ]   │
 │          Where do I find this? ↗                                │
 │                                                                │
 │   [ Cancel ]                                   ( Save securely ) │
 └────────────────────────────────────────────────────────────────┘
```

After Save:

```
 ┌────────────────────────────────────────────────────────────────┐
 │ ✓ Notion connected · token stored          [ Test ]  [ Undo ]   │
 └────────────────────────────────────────────────────────────────┘
```

## How the secret stays out (AUDIT §4.4 and T6)

1. **The value never enters the model's context.** The card posts it straight
   to the daemon over a dedicated route (proposed `POST /config/secret`, the
   same vault that `secret_set` and Connections write today). It is not sent
   as a chat message and not as a tool argument the model writes.
2. **The model sees only a receipt:** `{"secret": "notion.token", "status":
   "stored"}`. The transcript, the tool result, the ledger row and every log
   line say "set" or "replaced", never the value.
3. **Undo restores the prior state:**
   - if there was no secret before, Undo deletes the new one;
   - if there was a prior secret, Undo restores it. The previous encrypted
     value is kept in the vault under a short-lived backup entry; it is never
     decrypted into the ledger.
4. **If a user pastes a key into the composer by mistake,** the existing
   secret-shape masking (`detections/redact.mask`) catches it before sending:
   the message is held and the user is offered this card instead (proposed;
   AUDIT §9, Q9).
5. **The eye toggle** shows the typed value locally only, for checking a
   paste. It is not stored in the DOM after Save.

# Messaging surfaces — one conversation, every pocket

Status: DESIGN (2026-08-04). Not scheduled. Would land as the v1.136–v1.138
arc. Written after a code-grounded audit of comm/ — the starting point is
stronger than the Hermes comparison suggested.

## What already exists (do not rebuild)

- `comm/inbound.py InboundPoller` — a HARDENED two-way loop: opt-in per
  channel (`inbound_enabled` + credentials), fail-closed sender allowlist,
  private-chat-only guard, bot-loop protection, at-most-once durable offsets,
  `/command` grammar (status/workflows/run/runs/cancel/agents/ask/sessions),
  reflex keyword routing, and free-form text → supervised session → summary
  reply.
- Channels with a real inbound leg TODAY: **Telegram** (`poll` via
  getUpdates), **Email** (IMAP poll). Slack has send + a socket-mode module;
  Discord/Desktop/Console are send-only.
- Chat threads (`ChatThreadRecord`) are BROWSER-owned: the client assembles
  `messages_json` and autosaves via PUT. There is no server-side turn writer
  and no post-turn hook in chat.

## The actual gap

Inbound today is a COMMAND surface, not a CONVERSATION surface:

1. Every free-form message spawns an amnesiac one-shot session — no thread,
   no continuity ("what about the second one?" means nothing).
2. Replies bypass the chat lane — no memory recall, no skills, no project
   spine, no attachment/document machinery. The phone talks to a lesser
   Iron Jarvis.
3. Phone conversations are invisible on the desktop and vice versa — the
   opposite of Hermes's single unified history.
4. A parked workflow (v1.121 ask-gate) can notify the phone but the phone
   cannot ANSWER — the flagship "answer from a client meeting" loop is open.

## The design

### 1. Comm threads: server-owned chat threads

Add `owner: "user" | "daemon"` to ChatThreadRecord (additive column; default
"user" — zero behavior change for existing threads). A comm thread is
DAEMON-owned: the daemon appends inbound messages and its own replies
atomically; the dashboard renders it in the normal thread list (live via the
existing WS event feed; the chat page needs a comm-thread refresh path and
must treat daemon-owned threads as server-authoritative — no client
autosave clobbering).

### 2. Identity map

`CommIdentityRecord {channel, sender_id} → {thread_id, display_name}`.
Default: one thread per identity ("Telegram · Val"). Phase C adds "this is
me" identity merge: all of the user's identities feed ONE thread — the true
Hermes unified history. Allowlist membership is the trust boundary, as today.

### 3. The turn engine extraction (the keystone, and the risk center)

Factor the NON-STREAMING chat turn out of `routes/chat.py chat_complete`
into a service the HTTP route and the poller both call:
`chat_turn(thread, text, *, origin)` — system prompt assembly (memory
recall, project spine, lessons), skill "/" handling, tool arming, provider
routing, the tool loop, escalate-to-agent, thread persistence. Phone
surfaces don't stream, so the SSE path stays untouched — that halves the
refactor risk. chat.py is ~2200 lines; this is the one step that demands a
dedicated doer/reviewer pair and characterization tests BEFORE moving code.

### 4. Pocket reply semantics

- Immediate ack only when work will take long (escalated session/workflow):
  "On it — <short echo>". Otherwise just the answer.
- Chunk replies to the surface's limit (Telegram 4096); degrade markdown
  gracefully.
- Documents produced by a turn: add optional `Channel.send_file`; implement
  Telegram (sendDocument) + Email (attachment) first; surfaces without it
  get a path + "it's on your desktop".

### 5. Pending prompts — answer the machine from your pocket (killer feature)

`PendingPromptRecord {kind: workflow_ask | review | permission, ref_id,
question, options_json, thread_id, status}`. When a workflow parks at an ask
gate, the alert delivered to a chat-enabled destination REGISTERS a pending
prompt on that identity's thread. Then: numbered reply ("1"/"2") or free
text resolves it through the SAME atomic-claim answer path the chat card
uses (POST /workflows/runs/{id}/answer semantics — first answer wins,
restart-safe). Explicit `/answer <text>` always available; a bare reply
resolves the newest open prompt and echoes back what it did ("→ answered
the 'Which client?' gate on run intake-42"). Phase B extends to pending
reviews (approve/reject) and — carefully, maybe never — permission asks.

### 6. Continuity + budget

Thread history rides every turn, token-budgeted from the tail (reuse the
model_context_windows budgeting). `/new` starts a fresh comm thread (old one
stays in the desktop list). Because comm threads ARE chat threads,
commit-to-memory, crystallize-to-workflow, and Add-to-project work unchanged.

### 7. Security posture (unchanged where it's good, extended where new)

- Same fail-closed allowlist, private-chat-only, at-most-once offsets.
- Comm turns run the normal permission engine under the headless
  ask-resolver — dangerous tools fail closed; add a per-channel tool cap
  mirroring v1.121's workflow tool-step grant policy.
- EMAIL IS UNTRUSTED CONTENT: subject/body get SEC-1-style fencing (v1.99),
  and email stays command/reflex-only by DEFAULT — full-chat email is a
  separate opt-in, because inbound email is an injection surface in a way an
  allowlisted Telegram DM is not.
- The at-most-once rule stays: a crash mid-turn drops the message rather
  than replaying a remote-triggered action.

### 8. Surface roadmap

- **Telegram** — flagship (poll exists; richest phone UX).
- **Email** — second (IMAP poll exists; reply threading via Message-ID).
- **Slack** — wire the existing socket-mode module into the same engine.
- **Discord** — later (needs a receive leg).
- **WhatsApp / Signal — explicit non-goals for this arc.** WhatsApp Business
  API = approval + cost + phone-number plumbing; Signal = a signal-cli
  sidecar process. The architecture is channel-agnostic, so both can arrive
  later as just-another-channel without design changes.

### 9. Vocabulary

Two-way is a per-destination upgrade, not a new noun: the toggle reads
"Chat with Iron Jarvis from this destination". No "gateway", no new concept
word. Add the line to VOCABULARY.md in the shipping PR.

## Phasing

- **M-A (v1.136.0)** — comm thread core: owner column, identity map, turn
  extraction (non-streaming), Telegram full chat, desktop live view +
  reply-from-desktop fan-out (a desktop reply in a comm thread also sends
  out the channel). ~4 doer/reviewer pairs; the extraction pair goes first
  and alone.
- **M-B (v1.137.0)** — pending prompts: parked-run answers from the phone,
  /answer + numbered quick replies, pending-review approve/reject, command
  grammar extensions.
- **M-C (v1.138.0)** — email + Slack full chat (with email fencing),
  send_file both ways, Telegram voice memos → transcription → normal turn,
  identity merge ("one thread, every pocket").

## Open questions for build time

1. Does the chat page need a distinct read-only-while-typing treatment for
   daemon-owned threads, or is last-writer-wins acceptable with the server
   authoritative? (Recommend: server-authoritative + optimistic desktop
   sends through a new POST that routes into chat_turn.)
2. Rate limiting / cost guard for a runaway phone loop (bot-to-bot is
   guarded, but a user's forwarded-message flood isn't) — probably a simple
   per-identity turns-per-minute cap with an honest "slow down" reply.
3. Group chats stay refused (private-only guard) — revisit only with a
   compelling use case.

# One Claude Code program per coding reply (D4, v1.427.0)

The owner's decision of 2026-11-03, after the product-path benchmark (frozen
v1.426.2, 12 frontier tasks x 3, Sonnet medium): Iron Jarvis passed 36 of 36,
the same as Claude Code, but took 50.9 s a task against 40.1 s and sent 1.6 M
uncached input tokens against 0.6 M ($7.88 against $4.66 at list price). Each
step of a reply started a new `claude` (about 0.5 s) and replayed the whole
conversation into it, and the replay moved the cached prefix
(`docs/CLAUDE-NATIVE-ROUND-COST.md`).

## What changes

Inside an OPEN turn scope (`providers/turn_scope.py`: the lanes open one for
every coding-session turn that has a finish check), a claude-cli call keeps
its `claude` process between the reply's steps instead of ending it:

1. A call that cannot continue a kept process starts one as today, except
   `--max-turns` is large (the relay, not the CLI, ends a step) and stdin
   stays open.
2. When the model asks for tools, Claude Code denies them itself
   (`--permission-mode dontAsk`; the inert MCP inventory is unchanged) and at
   once sends its follow-up request, carrying the denials as `tool_result`
   blocks. The relay HOLDS that request; the call returns the tool calls to
   the lane, exactly as today; the process waits.
3. The lane runs the tools and calls again. When that call's conversation is
   exactly what the kept process holds plus the tool results for exactly the
   held calls (and, optionally, text or pictures after them in the same user
   turn), the relay puts the REAL results in place of the denials and lets
   the held request go. No process start, no replay.
4. When the model ends with text, Claude Code ends its query and waits on
   stdin. While the scope is open, the next call whose conversation is what
   the process holds plus one new user turn (the finish check's note, the
   closing-offer nudge) is written to stdin as a new message.
5. The relay applies every replacement again on every later request (Claude
   Code keeps the denials in its own history), byte for byte, so the cached
   prefix holds.

Anything else is a miss and today's path runs: the kept process is ended and
a new one replays the conversation. A miss is never an error.

## Measured on the real CLI (2.1.316, Haiku, 2026-11-03)

* After a tool step Claude Code denies the call (`system/permission_denied`),
  then sends the follow-up request within 30 ms.
* Held 5 s, then filled: the model answered from the filled result.
* Held 330 s (with `API_TIMEOUT_MS=3600000` in the CLI's env): the model
  answered from the filled result and the cached prefix was still read
  (cache read 972), so a step whose tools wait minutes on an approval card
  keeps its process. A parked process nobody resumes within 30 minutes is
  ended (the next call replays, as today).
* Five steps in one process (a two-call step, then three single calls), then
  a second question on stdin: one process, the right answer each time. Each
  request after the first read the earlier prefix from cache (cache read
  1,001 -> 1,182 -> 1,244 -> 1,306; cache write 62 a step).
* The process stayed alive after `result` and answered a new stdin message.
* Claude Code sends its own history again on every request: the denial text
  is in it each time, so the replacements are re-applied on every request.

## How it is built (`providers/adapters/claude_native/live.py`)

* **The route.** `LiveRoute` is a route on the ONE relay listener
  (`admission.listener()`): the `Handler` hands a request to a route that has
  a `handle` method, and the forwarding itself is the shared
  `Handler._relay` (the upstream pool, the TLS context, the proxy, the one
  sign-in retry per request). Each forwarded request has its own record
  (`admission.RequestRecord`: the attributes `transport.assemble`,
  `upstream_failure` and the Iron-Proxy attempt read), so those functions
  are reused as they are.
* **The hold.** When an answer that asks for tools is complete (before its
  last byte reaches the CLI), its `tool_use` ids become the ids the next
  request must answer; a request whose last message is a user turn with
  exactly those tool_result ids is held. Otherwise only the first request,
  one request per stdin message we wrote, or the sign-in retry goes
  upstream; everything else gets the CONSUMED refusal, as today.
* **The step's end.** A tool step ends when the relay holds the follow-up and
  the CLI's own denial line has been read (whichever comes second, within
  2 s); a text step ends at `result`. A denial whose follow-up never reaches
  the relay ends the step "unheld": the answer still goes back, the process
  is ended, the next call replays. A query that reaches `--max-turns` (1000)
  ends at the tool boundary the same way.
* **Resume.** Exactly what the process holds plus ONE user frame: after a
  held step, the leading tool_result blocks answer exactly the held ids (any
  order) and only text or pictures follow; after a text answer, no
  tool_result at all. The same system, model, effort, account (the whole
  Iron-Proxy home), tools and process. The miss reason (a word) is in the
  trace (`live_miss`); `live_new` / `live_resume` / `live_parked` /
  `live_full` / `live_unheld` mark the rest.
* **Lifetime.** Every session dies with its turn scope; a parked one nobody
  resumes within 1800 s is ended by a reaper; at most 4 at once (the oldest
  parked one makes room; none parked = today's path for that call); a miss
  ends the parked session of the same conversation (`transport._chain_key`)
  in the same scope before a new process starts; Stop, a walk-away or an
  error kills the tree; the held request is aborted whenever its session
  dies. A helper agent's run shares one process across its own steps but is
  never kept after its answer (the codex precedent). The daemon's shutdown
  stops them all (`live.stop_all`).
* **Off switch.** `IRONJARVIS_CLAUDE_LIVE=off`. The test suite runs with it off
  (`tests/conftest.py`): the older fake CLIs answer without the relay and
  only after stdin closes. `IJ_TEST_CLAUDE_LIVE=on` leaves it on.

## What stays the same

* Iron Jarvis executes every tool; the CLI only emits `tool_use`.
* One upstream request per model step: the relay admits the first request,
  each held follow-up once it is filled, and one request per stdin message;
  everything else is refused locally, as today.
* Outside an open turn scope (plain chat, agents, schedules, the browser
  sidebar), the adapter is byte-identical to v1.426.2.

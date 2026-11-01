# What one claude-cli round costs (C3.1 / C7, v1.420.0)

Chat › Code on Claude Code runs every model call as ONE `claude -p` process
(the v1.300.0 design: `--max-turns 1`, the inert MCP server, the loopback
admission relay, the history replayed frame by frame). This page is the
measured cost of that, phase by phase, and what v1.420.0 cut.

How it was measured: `claude_native.transport.TRACE` (off by default) gives
`(call, phase, ms since the call started, small numbers)` for every phase,
read with `time.perf_counter`. The relay stamps its own phases on its thread.

## The real run (before the change)

One real reply on this PC: Claude Code 2.1.316, `--model haiku`, this PC's own
sign-in, no Iron-Proxy, a scripted 3-round exchange (read a file, read the file
it names, answer), the daemon run from source. One run, so the numbers are one
sample each, not a distribution.

Milliseconds from the start of each call:

| Phase | Round 1 | Round 2 | Round 3 |
|---|---:|---:|---:|
| temp dir + 4 files written | 3 | 2 | 2 |
| relay listener bound | 8 | 3 | 3 |
| CLI process started | 58 | 11 | 11 |
| history acknowledged (frames) | 59 (0) | 511 (1) | 504 (2) |
| CLI `system init` seen | 789 | 522 | 510 |
| relay got the request | 823 | 546 | 526 |
| upstream TCP + TLS connected | 907 | 621 | 590 |
| upstream answer headers | 1318 | 1007 | 1182 |
| last model byte | 1538 | 1105 | 1185 |
| CLI `result` | 1561 | 1123 | 1193 |
| CLI exited (answer released then) | 1606 | 1156 | 1231 |
| teardown done | 1659 | 1170 | 1238 |
| what the caller waited, end to end | 1673 | 1176 | 1244 |

The same numbers as durations:

| Cost | Round 1 | Round 2 | Round 3 |
|---|---:|---:|---:|
| prepare (files + relay) | 8 | 3 | 3 |
| spawn | 50 | 8 | 8 |
| **CLI start, to its first answer line** | **731** | **500** | **493** |
| first line to the relay request | 34 | 24 | 16 |
| **new TCP + TLS handshake to Anthropic** | **84** | **75** | **64** |
| model, to its first byte | 411 | 386 | 592 |
| model, streaming | 220 | 98 | 3 |
| last byte to `result` | 23 | 18 | 8 |
| **`result` to exit (the answer waited for it)** | **45** | **33** | **38** |
| **inline teardown after the exit** | **53** | **14** | **7** |

What the model itself took (first byte plus streaming) was 484-631 ms a round;
everything else was 650-690 ms on the two warm rounds, and the CLI's own start
is about 500 ms of that.

History replay: the zero-turn acknowledgments came back as soon as the CLI was
up (in rounds 2 and 3, before its `init` line). Their cost is not separable
from the CLI's start, and nothing points at them, so writing the history as a
burst (C3.1 d) was NOT done: it could not save anything measurable here, and
the one-frame-at-a-time rule in `frames.py` (rule 1) stays as it is.

## What v1.420.0 changed

1. **The answer is released at `result`.** When the relay holds the whole 200
   answer and the CLI's one `result` is clean (success, or the `--max-turns 1`
   tool boundary with tool calls), the final frame goes to the caller at once.
   The CLI's exit, the reap, the reader joins, the relay route and the temp dir
   are torn down on a background thread. The next call of the SAME
   conversation (keyed by its first message and its account) waits for that
   teardown before it spawns, so a conversation never has two live CLIs. A CLI
   that does not exit within 3 s is killed with its tree. Any other ending (an
   error, a refusal, a Stop) tears down inline, exactly as before.
2. **One relay listener per process.** Each call is still its own admission on
   its own secret route (the admission key); an unknown or closed route is a
   404. Binding was only 1-8 ms; the real gain is the next point.
3. **One TLS context and a keep-alive upstream connection.** A connection whose
   answer was read to the end is kept (up to 30 s, 4 per upstream) and reused;
   one the server closed is caught before use, and if a kept connection dies
   under the request before any answer, ONE new connection carries it.

The admission rules are unchanged: the first request only, the one sign-in
retry, the proxy handling, the cache breakpoint pin.

## After (the real CLI against a loopback fake upstream, nothing spent)

Three replies of three rounds, the real Claude Code CLI going through the real
relay to a fake Anthropic on loopback (a scripted answer and a 50 ms stand-in
model), so the CLI's start and exit are real and no model was called:

- the final left at `result` in every call (0-1 ms later); the CLI exited
  30-45 ms after that, in the background, and its teardown took 2 ms more;
- one upstream connection served all 9 requests (8 of 9 reused);
- the next round of the same conversation waited 25-40 ms before spawning,
  because the scripted tool ran instantly. In the real lane, the work between
  rounds (the tool itself, the ledger, the reply record) overlaps that wait.

## What it saves, per round, on this PC

| | Saved |
|---|---:|
| TCP + TLS per round (every round within 30 s of the last) | 64-84 ms |
| exit + teardown, the LAST round of a reply | 40-100 ms |
| exit + teardown, a middle round | 0-45 ms (hidden behind the tool step) |

About 10-15% of a warm round's 1.1-1.3 s. The rest of the fixed cost is the
CLI's own start: about 500 ms a round.

## D4: a long-lived `claude` per session (not done; the owner's decision)

Keeping one CLI process per Chat › Code session (as `hosted/claude.py` already
does for Build › Code) would remove the spawn (8-50 ms) and the CLI's start
(about 500 ms, 731 ms cold) from every round after the first, and the history
replay with it. Projected on this PC: about 0.5 s a round, so roughly 1 s on a
3-round reply, 2.5 s on 6 rounds, 10 s on 20 rounds. It changes the v1.300.0
design (one admitted request per process, Iron Jarvis executing every tool,
account moves between rounds), so it waits for the decision.

## Not measured

- The packaged app: these numbers are from a source run. The packaged app
  starts the inert MCP server through the frozen exe's fast path; its start
  time was not measured. From source, `python -m` the inert server took a
  median 163 ms to answer `initialize` and `python -I <file>` 112 ms (5 runs
  each); whether Claude Code waits for that server before its first request
  was not measured.
- A second real run after the change (one real run was allowed); the "after"
  numbers come from the real CLI against a loopback upstream, and the TLS saving
  from the real run's own connect times.

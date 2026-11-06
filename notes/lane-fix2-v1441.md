# Lane fix2 (v1.441.0): the usage refresh and Build › Code's parity pieces, fixed

Review findings U1-U7, X1, X2, X5 (per-account usage refresh) and D1-D4
(Build › Code dashboard), from `scratchpad/review/findings.md`.

## What changed, for the user

- **Check usage now** no longer starts Claude Code or Codex when Iron-Proxy is
  too old to keep the number, or is not answering: the press says so at once
  (409, "Iron-Proxy needs an update to keep usage numbers." / "Iron-Proxy is
  not answering, so the usage could not be kept."), and the button is not
  offered (`can_refresh: false`) on such an Iron-Proxy.
- A login with no Claude plan says "This login has no Claude plan usage to
  report; sign in with a Claude plan to see it." instead of "Try again in a
  minute" forever.
- The background re-read of accounts in use:
  - skips a Build › Code reply parked on a card or a question (nothing is
    being spent; a night of a waiting card read the account every 3 min);
  - now covers a **Build terminal** started on an Iron-Proxy account while its
    Claude Code / Codex is working (the case that needed it: the real CLI talks
    to its vendor directly, so Iron-Proxy hears nothing);
  - no longer reads Chat › Code turns (their adapters already report the same
    windows after every model step);
  - backs off after answers with no numbers (doubling, at most 1 h; a no-plan
    login waits the hour at once). A press is never held back by it.
- A usage read never inherits an API key (Iron-Proxy's `COMMON_STRIP` names
  plus `CODEX_API_KEY` / `CODEX_ACCESS_TOKEN`), and no Claude child of Iron
  Jarvis inherits a parent Claude Code session's account
  (`CLAUDE_CODE_ACCOUNT_UUID` / `_USER_EMAIL` / `_ORGANIZATION_UUID`).
- The "Check usage now" note: a failed read's words wrap (no more cut on a
  phone) and the note's text is its tooltip; "as of N min ago" redraws itself
  every 30 s while the window is seen.
- Build › Code **Export as Markdown** writes the whole conversation: it reads
  every earlier page first (the "Load earlier" route, 2,000 events a page, at
  most 200 pages). Under the title it says what the file does not hold: steps
  the session's record trimmed, or an earlier part that could not be read. It
  is offered only once the session was read, and an empty session is refused in
  words (no empty file).
- A Claude Code **plan card** whose allow leaves the session in Full access
  takes a click, never Enter; the key hint names the button Enter presses
  ("Enter to approve, then ask first · Esc to deny"; "Click “Approve, then full
  access” to approve the plan · Esc to deny").
- **Copy reply** copies the whole reply (every finished text of the turn, from
  the user's message to its end, across its tool steps), once per reply; a
  helper's rows have no Copy button.

## What was measured, and how

- **X1 (no-plan answer)**: Claude Code 2.1.316 (`C:\Users\VR\.local\bin\claude.exe`),
  scratch `CLAUDE_CONFIG_DIR` holding no sign-in, every `CLAUDE*`/`ANTHROPIC*`
  name removed, `HTTPS_PROXY`/`HTTP_PROXY` = `http://127.0.0.1:9` (no network,
  no model call), through `usage_read._exchange` with `initialize` +
  `get_usage`. Answer: `{"session": {...}, "subscription_type": null,
  "rate_limits_available": false, "rate_limits": null, "behaviors": null}`.
  The fake CLI's new `noplan` mode replays that exact shape.
- **X5 (account variables)**: read from Claude Code 2.1.316's own bundle
  (`grep -a` of the binary): `let s=i.CLAUDE_CODE_ACCOUNT_UUID,
  h=i.CLAUDE_CODE_USER_EMAIL, y=i.CLAUDE_CODE_ORGANIZATION_UUID,
  S=!X()&&s&&h&&y?{accountUuid:s,emailAddress:h,organizationUuid:y}:null;
  if(S&&!ue().oauthAccount)MRn(S,n)` — with all three set and no
  `oauthAccount` yet, it writes them into the config's `.claude.json`. This
  very shell (a Claude Code session) has all three set, so a dev daemon passes
  them on. No test pinned the old marker set by value (the v1302 / v1303 pins
  read `CLAUDE_SESSION_MARKERS` itself and adapt).
- **U3 (Chat › Code already reports)**: read in code and pinned by the
  existing real-adapter test `tests/test_build_account_usage_v1368.py`
  (passes): `subprocess_cli.ClaudeCliAdapter` hands
  `response.extra["ratelimit_headers"]` (the relay's allow-listed
  `anthropic-ratelimit-*`, also on the live-session path through
  `RequestRecord.headers`) and `CodexNativeAdapter` hands `s.rate_limits` to
  `iron_proxy.accounts.report_success_async` after every model step of a
  leased (pinned) turn → Iron-Proxy `/finished`. Those are the very headers
  `usage_read.claude_headers` builds from `get_usage`, and Codex's
  `rateLimits` is the very snapshot `account/rateLimits/read` returns. A Build
  › Code (hosted) process and a Build terminal talk to the vendor directly, so
  only those two are polled.
- **U4**: Iron-Proxy's `COMMON_STRIP` read from the vendored bundle
  (`desktop/vendor/iron-proxy/iron-proxy.mjs`); Codex's own sign-in variables
  from `codex_native/adapter.py`'s docstring (0.157.0 source: they take
  precedence over the ChatGPT login).

## Pins

- `tests/test_usage_refresh_fixes_v14410.py` (15): U1/X2 refusals before any
  start, the route's 409, `can_refresh(row, svc)` and `accounts_view`; U2
  card-parked reply; U3 terminals (working/idle/blocked/dead/this-PC/unknown
  CLI, the real `TerminalManager.sessions()`), Chat › Code not read; U4/X5 env;
  X1 no-plan words; U7 back-off doubling, reset, the hour for no-plan, a press
  not held back.
- `dashboard/__tests__/build-code-review-fixes-v1441-0.test.tsx` (12): D1
  `collectAllEvents` (order, unread cases, page bound) and the real view's
  file (earlier pages, trimmed note, unreadable note); D2 failed read and
  empty session; D3 key rule, hint words, Enter through the view sends
  nothing; D4 `replyCopyTexts` (turns, carry-on) and through the view.
- `dashboard/__tests__/usage-refresh-fixes-v1441-0.test.tsx` (3): U5, U6.
- Moved on purpose: `tests/test_account_usage_read_v1439.py` (an outdated
  Iron-Proxy is now a refusal; a Chat › Code turn is not read),
  `dashboard/__tests__/build-code-parity-v1439.test.tsx` (the file lands a
  tick later: the export reads earlier pages first).
- Mutations (all RED): Python 12 — feature check, client check, `can_refresh`
  ignoring svc, card-parked counted, terminals not read, pane CLI filter, pane
  state check, keys not stripped, markers not dropped, no-plan read as unknown,
  no back-off, ok not resetting. Dashboard 9 — earlier pages not read, gap
  not said, offered before the read, empty file written, Enter into Full
  access, hint saying "allow once", copy per text piece, failed words cut, no
  tick.

## OPEN

- `CLAUDE_CODE_ACCOUNT_TAGGED_ID` (in the same bundle list) was not added: not
  asked, its use not read.
- A Build › Code session working in the BACKGROUND (helpers, a reply it started
  by itself) is still not "in use" for the re-read (only a running turn is).
- The terminal check uses each pane's output classifier; a pane whose CLI is
  not recognised counts every account it recorded (both providers).
- The export reads up to 200 × 2,000 events; a longer record is cut and says
  "could not be read".
- An API-key-only Claude login's `get_usage` shape was not measured (no key on
  this PC); a `subscription_type: null` with windows still reads as numbers.

## Draft CLAUDE.md bullet

- **Check usage now asks only when the number can be kept; the re-read covers
  what talks to the vendor directly; Build › Code's export is whole**
  (v1.441.0, review U1-U7, X1, X2, X5, D1-D4). `usage_read._row_and_lease`
  refuses `proxy_update` (no `plan-usage`) and `proxy_not_answering` BEFORE the
  CLI starts; `can_refresh(row, svc)` / `decorate(row, svc)` say False then
  (every caller passes the service). `in_use_accounts` = Build › Code turns
  that run and have no open card/question (`live.asks`), plus Build terminals
  (`TerminalManager.sessions()`) whose recorded `accounts` row names the
  account and whose activity is `working` (the CLI picks the provider);
  Chat › Code is NOT read — its adapters report the same windows after every
  step (`report_success`, pinned in `test_build_account_usage_v1368`).
  Claude Code's measured no-plan answer (`rate_limits_available: false`) is
  `NO_PLAN_WORDS`; empty answers back the loop off (`_EMPTY`, doubling to
  `EMPTY_BACKOFF_MAX_S` 3600). A read drops `USAGE_READ_STRIP` (Iron-Proxy's
  COMMON_STRIP + CODEX_API_KEY/CODEX_ACCESS_TOKEN); `CLAUDE_SESSION_MARKERS`
  gains the parent's ACCOUNT_UUID/USER_EMAIL/ORGANIZATION_UUID (Claude Code
  writes them as `oauthAccount`). Export as Markdown: `collectAllEvents` reads
  every page before the pane's (2,000 a page, ≤ 200), `exportGapWords` says
  trimmed / unreadable under the title, offered only after the session was read,
  an empty one refused (`EXPORT_NOTHING`). Plan card: Enter refused when
  `thenMode`/`setsMode` is `full`; `codeAskKeyHint` names the plan button.
  Copy reply: `replyCopyTexts` (the turn's finished texts, on its last one; a
  carry-on is not a new turn; helpers none). Pins:
  `tests/test_usage_refresh_fixes_v14410.py`,
  `dashboard/__tests__/{build-code-review-fixes,usage-refresh-fixes}-v1441-0.test.tsx`.

## Draft Handbook

**Check usage now.** The button reads an account's usage only when Iron-Proxy
can keep the number; an older Iron-Proxy, or one that is not answering, says so
right away. A login with no Claude plan says it has no plan usage to report.
While a coding session or a Build terminal works on an account, Iron Jarvis
re-reads its usage every few minutes (less often when the login reports no
numbers); a session waiting for your answer is not re-read.

**Export as Markdown** (Build › Code, the session's ⋯ menu) saves the whole
conversation, including the parts not on screen. If the session's record trimmed
its oldest steps, or an earlier part could not be read, the file says so right
under its title.

**Plan cards.** Enter approves a plan only when it does not move the session to
Full access; the line under the card names the button Enter presses.

**Copy reply** copies the whole reply, including the words before and after its
steps.

## Review (independent reviewer)

Every claim checked against the code; the builder's pins mutation-checked
(Python 9 + dashboard 5, all RED; `_no_plan`'s first branch alone survives
because its second branch covers the measured shape too — the whole check
mutated is RED).

Re-measured: X1 on the real Claude Code 2.1.316 through `usage_read._ask_claude`,
scratch `CLAUDE_CONFIG_DIR`/`USERPROFILE`/`HOME`, every `CLAUDE*`/`ANTHROPIC*`
name removed, proxy `http://127.0.0.1:9` (no network, no model call): the read
answers `NO_PLAN_WORDS` with `empty=16`. X5 re-read in the same binary: the
three account variables seed `oauthAccount` only (`oss`) and feed OTEL
attributes; they are set by a host Claude Code, not a user setting, so
dropping them loses nothing a user sets.

Defects found and fixed (each pin red first):

1. **A Build terminal stuck at "working" was read forever (U3).** The
   classifier reads the pane's tail, which stands still while the pane prints
   nothing, so a screen frozen on a working line (a hung CLI) counted as in use
   for the pane's whole life: the account's CLI was started every 3 min all
   night. Now a pane counts only when it printed within `IN_USE_EVERY_S`
   (`TerminalSession.last_output_at`; a working TUI repaints about once a
   second). Pins: `tests/test_usage_refresh_review_v14410.py`
   (`…printed_nothing_for_minutes…`, `…no_output_time…`).
2. **A plain "+" terminal counted every provider (U3).** With Iron-Proxy on,
   `pane_accounts.resolve` records the first free account of EVERY provider for
   a plain pane; a dev server or test watcher printing "Server running…" read
   `working`, and an unknown CLI counted every recorded account, so Claude
   Code AND Codex were started for a shell that runs neither. Now only a pane
   whose CLI is Claude Code or Codex counts, and only its own provider
   (`…plain_shell_on_every_providers…`; the builder's unknown-CLI expectation
   in `test_usage_refresh_fixes_v14410` moved on purpose, its doubles gained
   `last_output_at`).
3. **Export as Markdown said nothing while it read (D1).** The menu closed at
   once and up to 200 sequential page reads followed in silence; a second
   press read every page again and wrote a second file; leaving the view kept
   reading and dropped a file after the user left. Now a quiet
   `code-export-status` line says `EXPORT_READING` while it reads, a press while
   one runs does nothing, and an unmounted view reads no further page and
   writes no file. Pins: `dashboard/__tests__/build-code-export-review-v1441-0.test.tsx`
   (2; mutations of the guard, the two "left" checks and the status line all RED).

Checked and fine: the back-off is per account (`_EMPTY` keyed by account),
reset by a read that works, in memory only (`reset()` clears it); a press is
never held back. A "this PC" pane (`id: None`) adds nothing. The export's
cursor is taken once at the press (the pane's `older` and its held events), so
events written during the read are simply not in the file, never doubled. D4: a
steer note does not end a reply (its words before and after join); a carry-on
user message does not either; helper rows render without `copyText`.

OPEN (review):

- The export's pages are joined with a copy per page and folded on the main
  thread: a 400,000-event record can freeze the page for seconds at the end.
- `CLAUDE_CODE_OAUTH_SCOPES` / `CLAUDE_CODE_SUBSCRIPTION_TYPE` /
  `CLAUDE_CODE_RATE_LIMIT_TIER` (also host-injected; this shell has the first)
  are still passed on; they matter only beside `CLAUDE_CODE_OAUTH_TOKEN`.
- A pane whose Claude Code the classifier cannot name (the welcome box
  scrolled out of the 32 KB tail and no Launch hint) is no longer counted.
- A read that times out (not an empty answer) is still retried every 3 min.

"""v1.441.0 lane fix2, REVIEW: what counts as a Build terminal "in use".

The builder's rule counted a pane whose activity reads ``working`` — but the
classifier reads the last few lines of the pane's TAIL, which stands still
when the pane prints nothing. Two ways that polled the vendors forever:

* a pane whose screen ended on a working-looking line and then went quiet
  (a hung CLI, a closed laptop lid's frozen screen) stayed ``working`` for as
  long as the pane lived: the account's usage CLI was started every three
  minutes all night. A CLI that really works repaints its status line about
  once a second (``TerminalSession.last_output_at``'s own comment), so a pane
  silent for ``IN_USE_EVERY_S`` is not working.
* with Iron-Proxy on, a plain "+" terminal records the first free account of
  EVERY provider (``pane_accounts.resolve``), and a dev server or a test
  watcher in it prints "Server running…" lines: an unknown CLI counted every
  recorded account, so Claude Code AND Codex were started for a shell that
  runs neither. Only a pane whose CLI is known (Claude Code or Codex) counts.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from iron_jarvis.iron_proxy import usage_read as U


class _Pane:
    def __init__(self, accounts, state, cli, *, quiet_s=0.0, alive=True):
        self.accounts = accounts
        self.alive = alive
        self.last_output_at = time.monotonic() - quiet_s
        self._act = SimpleNamespace(state=SimpleNamespace(value=state), cli=cli)

    def activity(self):
        return self._act


def _platform(panes):
    return SimpleNamespace(hosted=None, terminals=SimpleNamespace(sessions=lambda: panes),
                           turn_logs=None, engine=None)


def test_a_pane_that_still_reads_working_but_printed_nothing_for_minutes_is_not_in_use():
    panes = [
        _Pane({"anthropic": {"id": "acct_live"}}, "working", "claude", quiet_s=2.0),
        _Pane({"anthropic": {"id": "acct_frozen"}}, "working", "claude",
              quiet_s=U.IN_USE_EVERY_S + 5),
    ]
    assert U.in_use_accounts(_platform(panes)) == ["acct_live"]


def test_a_plain_shell_on_every_providers_first_free_account_is_never_in_use():
    # A "+" terminal with Iron-Proxy on: a dev server prints, nobody runs a CLI.
    plain = _Pane({"anthropic": {"id": "acct_a"}, "openai": {"id": "acct_o"},
                   "xai": {"id": "acct_x"}}, "working", None, quiet_s=0.5)
    assert U.in_use_accounts(_platform([plain])) == []


def test_a_pane_with_no_output_time_is_not_in_use():
    pane = _Pane({"anthropic": {"id": "acct_t"}}, "working", "claude")
    pane.last_output_at = 0.0  # the session's own "never printed"
    assert U.in_use_accounts(_platform([pane])) == []


@pytest.mark.parametrize("cli,provider", [("claude", "anthropic"), ("codex", "openai")])
def test_a_working_known_cli_still_counts_only_its_own_provider(cli, provider):
    pane = _Pane({"anthropic": {"id": "acct_a"}, "openai": {"id": "acct_o"}}, "working", cli,
                 quiet_s=1.0)
    want = "acct_a" if provider == "anthropic" else "acct_o"
    assert U.in_use_accounts(_platform([pane])) == [want]

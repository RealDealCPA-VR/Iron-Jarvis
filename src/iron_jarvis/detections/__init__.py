"""Detections (v1.290.0): read what an agent DID and say when it looks bad.

A small, dependency-free rule engine over one normalized EVENT shape (a plain
dict — ``action`` + ``session_id`` + optional ``tool``/``command``/``path``/
``url``/``text``/``ts``/``ok``/``ref``/``source``). Three producers feed it:
this app's own tool ledger (:mod:`.ledger`), and the Claude Code / Codex
history reader (``iron_jarvis.history``). One consumer shape comes out:
:class:`Finding`.

It DETECTS; it never blocks. Nothing here sits in a tool's path — a finding is
a note about something that already happened, surfaced on the Detections
routes and (for high/critical ones) once on the bell after a session ends.

Rules live as YAML beside this file (``rules/*.rule.yaml``). The condition is a
small declarative matcher, not CEL (no new dependency): see :mod:`.rules`.
Every rule carries its own ``match`` and ``no_match`` fixtures, and a rule
without both is refused at load — the test suite runs every fixture.

ATTRIBUTION. Most of the packaged rules are ADAPTED from agent-beacon
(https://github.com/Asymptote-Labs/agent-beacon, MIT License, Copyright (c)
2026 Asymptote Labs) — its ``rules/`` catalog and
``pkg/asymptoteobserve/rulestore/baseline``: credential-file-read (folding in
kubernetes-secret-file-read), browser-session-store-read,
password-manager-db-read, credentials-in-curl-data, curl-pipe-to-shell
(folding in base64-decode-piped-to-shell), recursive-root-delete,
secret-read-then-egress, approval-denied-then-dangerous (from
approval-denied-command-executed-anyway + approval-bypass-via-renamed-binary),
persistence-autostart (from persistence-install-command +
shell-login-dotfile-modified), git-history-rewrite,
git-force-push-to-protected, tool-output-prompt-injection (from
ignore-previous-instructions), disk-fill-attempt, fork-bomb-pattern,
cloud-metadata-endpoint-access, upload-to-paste-transfer-site and
env-dump-piped-to-network. The CEL conditions were rewritten into this
matcher and every regex gained its Windows forms (cmd, PowerShell,
``%USERPROFILE%``, backslash paths). ironjarvis-secret-key-read,
security-tool-disabled and windows-credential-dump are this app's own. The
MIT licence text is in ``rules/THIRD_PARTY_LICENSE.txt``.
"""

from __future__ import annotations

from .engine import Finding, scan
from .rules import Rule, RuleError, load_rules, parse_rule, run_rule_tests

__all__ = [
    "Finding",
    "Rule",
    "RuleError",
    "load_rules",
    "parse_rule",
    "run_rule_tests",
    "scan",
]

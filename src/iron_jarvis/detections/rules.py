"""Rule loader + the declarative matcher (v1.290.0).

A rule file is YAML in agent-beacon's shape — ``id``, ``version``, ``title``,
``description``, ``severity``, ``status``, ``posture``, ``category``,
``taxonomy``, ``emit.reason``, ``tests[]`` — but its condition is NOT CEL. It
is one of:

``match:`` — ONE event matches when every key holds::

    match:
      action: file.read                 # a string or a list (membership)
      path:                             # any other event field
        regex: '...'                    # str or list: EVERY one must search()
        regex_any: ['...', '...']       # at least one must search()
        not_regex: '...'                # str or list: NONE may search()
        contains: [git]                 # cheap gate: one of these words occurs
        regex_kinds:                    # {kind: [regex, ...]}: at least one
          delete: ['...']               #   kind must match; the kinds that did
                                        #   are what `same_kind` compares
        equals: ...                     # exact value
      any:                              # optional: at least one sub-match holds
        - {action: ..., command: {...}}

``correlation:`` — an ORDERED sequence inside one session::

    correlation:
      scope: session
      window: 120                       # seconds (or "120s" / "5m")
      same_kind: true                   # optional: every step shares a kind
                                        #   with the first (see regex_kinds)
      steps:
        - {id: read_secret, match: {...}}
        - {id: egress, match: {...}}

Every regex is case-insensitive ``re.search``. A field the event does not
carry never matches a regex (absence is not a match). Unknown keys are
REFUSED at load — a typo like ``regx`` must not silently match everything.

BOUNDED INPUT (v1.290.0 review). Commands are matched ONE LINE AT A TIME
(a multi-line script is many commands; every rule's segment class also stops
at a newline), each line cut at ``LINE_CAP`` and the whole command at
``COMMAND_CAP``; every other field is cut at ``FIELD_CAP``. Real commands
reach tens of KB and a backtracking regex over an unbounded string is a
denial of service on the scan — the cap is what bounds the worst case, and
the pin in ``tests/test_detections_v1290.py`` proves it by ratio.

Every rule must carry at least one ``match`` AND one ``no_match`` fixture;
``load_rules`` refuses a rule without both, and the test suite runs every
fixture of every packaged rule (``run_rule_tests``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import yaml

#: Where the packaged rule set lives.
RULES_DIR = Path(__file__).with_name("rules")

SEVERITIES = ("low", "medium", "high", "critical")
SEVERITY_RANK = {name: i for i, name in enumerate(SEVERITIES)}

#: The event actions a rule may name (the shared EVENT contract).
ACTIONS = frozenset(
    {
        "command.executed",
        "file.read",
        "file.write",
        "file.delete",
        "network.request",
        "tool.called",
        "approval.denied",
        "prompt.submitted",
        "response.completed",
    }
)

#: The event fields a matcher may test besides ``action``.
FIELDS = frozenset(
    {"session_id", "source", "tool", "command", "path", "url", "text", "ok", "ref"}
)

_FIELD_SPEC_KEYS = frozenset(
    {"regex", "regex_any", "not_regex", "regex_kinds", "contains", "equals"}
)

#: Longest single command line a regex ever sees.
LINE_CAP = 4000
#: Longest command (all lines) a rule ever reads.
COMMAND_CAP = 64000
#: Longest path / url / text / other field a regex ever sees.
FIELD_CAP = 4000
_RULE_KEYS = frozenset(
    {
        "id",
        "version",
        "title",
        "description",
        "severity",
        "status",
        "posture",
        "category",
        "taxonomy",
        "adapted_from",
        "match",
        "correlation",
        "emit",
        "tests",
    }
)


class RuleError(ValueError):
    """A rule file that cannot be trusted to run. Names the rule and the fault."""


#: A regex that opens with ``\b(word|word|...)`` or ``\bword`` can only match
#: where one of those words appears — found with a plain substring test, which
#: is far cheaper than letting the regex engine try every position.
_LEAD_GROUP = re.compile(r"^\\b\((?:\?:)?((?:[A-Za-z0-9_-]|\\\.)+(?:\|(?:[A-Za-z0-9_-]|\\\.)+)*)\)")
_LEAD_WORD = re.compile(r"^\\b((?:[A-Za-z0-9_-]|\\\.)+)")


def _required_words(pattern: str) -> tuple[str, ...]:
    """Lower-cased words one of which MUST occur for *pattern* to match, or ()
    when that cannot be read off its opening (then the regex always runs)."""
    m = _LEAD_GROUP.match(pattern)
    if m is None:
        m = _LEAD_WORD.match(pattern)
    if m is None or pattern[m.end():m.end() + 1] in ("?", "*", "{"):
        return ()
    return tuple(w.replace("\\.", ".").lower() for w in m.group(1).split("|"))


@dataclass(frozen=True)
class Rx:
    """A compiled rule regex plus its cheap pre-check (see _required_words)."""

    regex: re.Pattern
    words: tuple[str, ...] = ()

    @property
    def pattern(self) -> str:
        return self.regex.pattern

    def search(self, text: str, low: str | None = None):
        if self.words:
            low = text.lower() if low is None else low
            if not any(w in low for w in self.words):
                return None
        return self.regex.search(text)


def _compile(pattern: Any, where: str) -> Rx:
    if not isinstance(pattern, str) or not pattern:
        raise RuleError(f"{where}: a regex must be a non-empty string")
    try:
        return Rx(re.compile(pattern, re.IGNORECASE), _required_words(pattern))
    except re.error as exc:
        raise RuleError(f"{where}: bad regex {pattern!r}: {exc}") from exc


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else [value]


#: ``<<EOF`` / ``<<-'EOF'`` / ``<< "EOF"`` — a heredoc marker and its word.
_HEREDOC = re.compile(r"""<<(-?)[ \t]*(['"]?)([A-Za-z_][\w.-]*)\2""")
#: The command a heredoc feeds is a plain file write (cat / tee).
_DATA_SINK = re.compile(r"^\s*(sudo\s+)?(cat|tee)\b", re.IGNORECASE)


@lru_cache(maxsize=2048)
def command_lines(text: str) -> tuple[str, ...]:
    """A command as the rules see it: one entry per line, capped, with the
    BODY of a heredoc that is only written to a file (``cat > f <<'EOF'``,
    ``tee f <<EOF``) left out — that body is file content, not something the
    shell ran, and judging it as commands flagged every script that WRITES a
    rule's own fixtures. A heredoc fed to a shell or an interpreter keeps its
    body: that text is executed."""
    lines = text[:COMMAND_CAP].splitlines() or [""]
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line[:LINE_CAP])
        i += 1
        m = _HEREDOC.search(line)
        if m is None:
            continue
        segment = re.split(r"[;&|]", line[: m.start()])[-1]
        after = line[m.end():]
        if not _DATA_SINK.search(segment) or "|" in after:
            continue
        word = m.group(3)
        while i < len(lines) and lines[i].strip() != word:
            i += 1  # file content: skipped
        # the terminator line itself (if present) is skipped too
        i += 1
    return tuple(out)


@dataclass(frozen=True)
class FieldTest:
    """One field's condition: all of ``regex``, one of ``regex_any``, none of
    ``not_regex``, one kind of ``regex_kinds``, and ``equals`` when given —
    all judged on the SAME piece (a command line, or the capped field)."""

    name: str
    all_of: tuple[Rx, ...] = ()
    any_of: tuple[Rx, ...] = ()
    none_of: tuple[Rx, ...] = ()
    kinds: tuple[tuple[str, tuple[Rx, ...]], ...] = ()
    #: Cheap gate: at least one of these words (case-insensitive) must occur.
    contains: tuple[str, ...] = ()
    has_equals: bool = False
    equals: Any = None

    @property
    def has_regex(self) -> bool:
        return bool(
            self.all_of or self.any_of or self.none_of or self.kinds or self.contains
        )

    def _pieces(self, value: Any) -> list[str]:
        text = value if isinstance(value, str) else str(value)
        if self.name == "command":
            return list(command_lines(text))
        return [text[:FIELD_CAP]]

    def _piece_kinds(self, piece: str) -> frozenset[str] | None:
        """None when *piece* fails; else the kinds it matched (maybe empty)."""
        low = piece.lower()
        if self.contains and not any(w in low for w in self.contains):
            return None
        if any(p.search(piece, low) is None for p in self.all_of):
            return None
        if self.any_of and not any(p.search(piece, low) for p in self.any_of):
            return None
        if any(p.search(piece, low) for p in self.none_of):
            return None
        if not self.kinds:
            return frozenset()
        hit = frozenset(
            k for k, pats in self.kinds if any(p.search(piece, low) for p in pats)
        )
        return hit or None

    def matched_kinds(self, event: dict) -> frozenset[str] | None:
        """None when the condition fails; else the kinds that matched."""
        value = event.get(self.name)
        if self.has_equals and value != self.equals:
            return None
        if not self.has_regex:
            return frozenset()
        if value is None:
            return None  # absence is never a regex match
        found: frozenset[str] | None = None
        for piece in self._pieces(value):
            got = self._piece_kinds(piece)
            if got is None:
                continue
            if not self.kinds:
                return got
            found = got if found is None else found | got
        return found

    def holds(self, event: dict) -> bool:
        return self.matched_kinds(event) is not None


@dataclass(frozen=True)
class Matcher:
    """One event-level condition. Every part must hold."""

    actions: frozenset[str] | None = None
    fields: tuple[FieldTest, ...] = ()
    alternatives: tuple["Matcher", ...] = ()

    def matched_kinds(self, event: dict) -> frozenset[str] | None:
        """None when the event does not match; else the union of the kinds
        its ``regex_kinds`` fields matched (empty when it has none)."""
        if self.actions is not None and event.get("action") not in self.actions:
            return None
        kinds: frozenset[str] = frozenset()
        for f in self.fields:
            got = f.matched_kinds(event)
            if got is None:
                return None
            kinds |= got
        if self.alternatives:
            alt = [m.matched_kinds(event) for m in self.alternatives]
            hits = [k for k in alt if k is not None]
            if not hits:
                return None
            for k in hits:
                kinds |= k
        return kinds

    def matches(self, event: dict) -> bool:
        return self.matched_kinds(event) is not None


def _parse_matcher(spec: Any, where: str) -> Matcher:
    if not isinstance(spec, dict) or not spec:
        raise RuleError(f"{where}: a match must be a non-empty mapping")
    actions: frozenset[str] | None = None
    fields: list[FieldTest] = []
    alternatives: list[Matcher] = []
    for key, value in spec.items():
        if key == "action":
            names = _as_list(value)
            bad = [n for n in names if n not in ACTIONS]
            if bad or not names:
                raise RuleError(f"{where}: unknown action(s) {bad or names}")
            actions = frozenset(names)
        elif key == "any":
            subs = _as_list(value)
            if not subs:
                raise RuleError(f"{where}: `any` needs at least one sub-match")
            alternatives.extend(
                _parse_matcher(s, f"{where}.any[{i}]") for i, s in enumerate(subs)
            )
        elif key in FIELDS:
            if not isinstance(value, dict) or not value:
                raise RuleError(f"{where}.{key}: a field test must be a mapping")
            unknown = set(value) - _FIELD_SPEC_KEYS
            if unknown:
                raise RuleError(f"{where}.{key}: unknown key(s) {sorted(unknown)}")
            fw = f"{where}.{key}"
            raw_kinds = value.get("regex_kinds") or {}
            if not isinstance(raw_kinds, dict):
                raise RuleError(f"{fw}: regex_kinds must map a kind to regexes")
            fields.append(
                FieldTest(
                    name=key,
                    all_of=tuple(
                        _compile(p, fw) for p in _as_list(value.get("regex") or [])
                    ),
                    any_of=tuple(
                        _compile(p, fw) for p in _as_list(value.get("regex_any") or [])
                    ),
                    none_of=tuple(
                        _compile(p, fw) for p in _as_list(value.get("not_regex") or [])
                    ),
                    kinds=tuple(
                        (str(k), tuple(_compile(p, f"{fw}.{k}") for p in _as_list(v)))
                        for k, v in raw_kinds.items()
                    ),
                    contains=tuple(
                        str(w).lower() for w in _as_list(value.get("contains") or [])
                    ),
                    has_equals="equals" in value,
                    equals=value.get("equals"),
                )
            )
        else:
            raise RuleError(f"{where}: unknown match key {key!r}")
    return Matcher(actions=actions, fields=tuple(fields), alternatives=tuple(alternatives))


def _parse_window(value: Any, where: str) -> float:
    if isinstance(value, bool):
        raise RuleError(f"{where}: window must be seconds")
    if isinstance(value, (int, float)):
        seconds = float(value)
    else:
        m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smh]?)\s*", str(value or ""))
        if not m:
            raise RuleError(f"{where}: window {value!r} is not a duration")
        seconds = float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600}[m.group(2)]
    if seconds <= 0:
        raise RuleError(f"{where}: window must be positive")
    return seconds


@dataclass(frozen=True)
class Step:
    id: str
    matcher: Matcher


@dataclass(frozen=True)
class Correlation:
    window_s: float
    steps: tuple[Step, ...]
    #: Every later step must share a ``regex_kinds`` kind with the first.
    same_kind: bool = False


@dataclass(frozen=True)
class Rule:
    """One validated rule. Build with :func:`parse_rule`, never by hand."""

    id: str
    version: int
    title: str
    description: str
    severity: str
    status: str
    posture: str
    category: str
    reason: str
    taxonomy: dict = field(default_factory=dict)
    adapted_from: str = ""
    matcher: Matcher | None = None
    correlation: Correlation | None = None
    tests: tuple[dict, ...] = ()
    source_file: str = ""

    def summary(self) -> dict[str, Any]:
        """The public listing shape (``GET /detections/rules``)."""
        return {
            "id": self.id,
            "title": self.title,
            "severity": self.severity,
            "description": self.description,
            "category": self.category,
            "kind": "correlation" if self.correlation else "match",
            "adapted_from": self.adapted_from,
        }


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def parse_rule(doc: Any, source_file: str = "") -> Rule:
    """Validate one rule document; raise :class:`RuleError` naming the fault."""
    where = source_file or "<rule>"
    if not isinstance(doc, dict):
        raise RuleError(f"{where}: a rule must be a mapping")
    rid = str(doc.get("id") or "").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", rid):
        raise RuleError(f"{where}: missing or malformed id {rid!r}")
    where = f"{rid}"
    unknown = set(doc) - _RULE_KEYS
    if unknown:
        raise RuleError(f"{where}: unknown key(s) {sorted(unknown)}")
    for key in ("title", "description", "category"):
        if not _clean_text(doc.get(key)):
            raise RuleError(f"{where}: `{key}` is required")
    severity = str(doc.get("severity") or "")
    if severity not in SEVERITIES:
        raise RuleError(f"{where}: severity must be one of {SEVERITIES}")
    emit = doc.get("emit") or {}
    reason = _clean_text(emit.get("reason") if isinstance(emit, dict) else "")
    if not reason:
        raise RuleError(f"{where}: `emit.reason` is required")

    has_match = "match" in doc
    has_corr = "correlation" in doc
    if has_match == has_corr:
        raise RuleError(f"{where}: exactly one of `match` or `correlation` is required")
    matcher: Matcher | None = None
    correlation: Correlation | None = None
    if has_match:
        matcher = _parse_matcher(doc["match"], f"{where}.match")
    else:
        corr = doc["correlation"]
        if not isinstance(corr, dict):
            raise RuleError(f"{where}.correlation: must be a mapping")
        unknown_c = set(corr) - {"scope", "window", "steps", "same_kind"}
        if unknown_c:
            raise RuleError(f"{where}.correlation: unknown key(s) {sorted(unknown_c)}")
        if str(corr.get("scope") or "session") != "session":
            raise RuleError(f"{where}.correlation: only scope `session` is supported")
        raw_steps = corr.get("steps")
        if not isinstance(raw_steps, list) or len(raw_steps) < 2:
            raise RuleError(f"{where}.correlation: needs at least two steps")
        steps: list[Step] = []
        for i, s in enumerate(raw_steps):
            if not isinstance(s, dict) or not str(s.get("id") or "").strip():
                raise RuleError(f"{where}.correlation.steps[{i}]: needs an id")
            steps.append(
                Step(
                    id=str(s["id"]),
                    matcher=_parse_matcher(
                        s.get("match"), f"{where}.correlation.steps[{i}]"
                    ),
                )
            )
        correlation = Correlation(
            window_s=_parse_window(corr.get("window"), f"{where}.correlation"),
            steps=tuple(steps),
            same_kind=bool(corr.get("same_kind", False)),
        )

    tests = doc.get("tests")
    if not isinstance(tests, list) or not tests:
        raise RuleError(f"{where}: needs `tests` — at least one match and one no_match")
    verdicts = set()
    for i, t in enumerate(tests):
        if not isinstance(t, dict):
            raise RuleError(f"{where}.tests[{i}]: must be a mapping")
        verdict = t.get("verdict")
        if verdict not in ("match", "no_match"):
            raise RuleError(f"{where}.tests[{i}]: verdict must be match or no_match")
        events = t.get("events")
        if not isinstance(events, list) or not events:
            raise RuleError(f"{where}.tests[{i}]: needs events")
        for j, ev in enumerate(events):
            if not isinstance(ev, dict) or ev.get("action") not in ACTIONS:
                raise RuleError(f"{where}.tests[{i}].events[{j}]: needs a known action")
        verdicts.add(verdict)
    if verdicts != {"match", "no_match"}:
        raise RuleError(
            f"{where}: needs at least one `match` AND one `no_match` fixture "
            f"(has {sorted(verdicts)})"
        )
    try:
        version = int(doc.get("version") or 1)
    except (TypeError, ValueError) as exc:
        raise RuleError(f"{where}: version must be an integer") from exc
    taxonomy = doc.get("taxonomy") or {}
    return Rule(
        id=rid,
        version=version,
        title=_clean_text(doc["title"]),
        description=_clean_text(doc["description"]),
        severity=severity,
        status=str(doc.get("status") or "stable"),
        posture=str(doc.get("posture") or "detect"),
        category=_clean_text(doc["category"]),
        reason=reason,
        taxonomy=dict(taxonomy) if isinstance(taxonomy, dict) else {},
        adapted_from=_clean_text(doc.get("adapted_from")),
        matcher=matcher,
        correlation=correlation,
        tests=tuple(tests),
        source_file=source_file,
    )


def load_rule_file(path: Path) -> Rule:
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RuleError(f"{path.name}: not valid YAML: {exc}") from exc
    return parse_rule(doc, source_file=path.name)


def load_rules_from(directory: Path) -> list[Rule]:
    """Load + validate every ``*.rule.yaml`` in *directory*. Any bad rule, or a
    duplicate id, raises — a half-loaded rule set would under-report silently."""
    rules: list[Rule] = []
    seen: set[str] = set()
    for path in sorted(Path(directory).glob("*.rule.yaml")):
        rule = load_rule_file(path)
        if rule.id in seen:
            raise RuleError(f"{path.name}: duplicate rule id {rule.id!r}")
        seen.add(rule.id)
        rules.append(rule)
    if not rules:
        raise RuleError(f"no rules found in {directory}")
    return rules


@lru_cache(maxsize=1)
def _packaged() -> tuple[Rule, ...]:
    return tuple(load_rules_from(RULES_DIR))


def load_rules() -> list[Rule]:
    """The packaged rule set, validated; cached for the process."""
    return list(_packaged())


def run_rule_tests(rule: Rule) -> list[tuple[str, str, bool]]:
    """Run a rule's embedded fixtures: ``[(name, expected_verdict, passed)]``.

    Fixture events without a ``session_id`` share one (``"fixture"``)."""
    from .engine import scan

    results: list[tuple[str, str, bool]] = []
    for i, t in enumerate(rule.tests):
        events: Iterable[dict] = [
            {"session_id": "fixture", **ev} for ev in t.get("events") or []
        ]
        fired = bool(scan(events, [rule]))
        expected = t["verdict"]
        results.append(
            (str(t.get("name") or f"test{i}"), expected, fired == (expected == "match"))
        )
    return results

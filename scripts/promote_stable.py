"""Move the STABLE update channel forward (v1.405.0).

    uv run --no-project python scripts/promote_stable.py --dir BRANCH_DIR --repo OWNER/NAME
        [--hold X.Y.Z] [--unhold X.Y.Z] [--promote X.Y.Z]

WHY. 55 releases went out in 7 days (7 on 2026-10-28). v1.400.9 broke the
dashboard type check, v1.401.0's installer failed until v1.401.1, and the first
start after an update hung on 2026-10-27. The user's daily-driver PC should not
be the first to meet a release, so it can read the STABLE channel: a release
reaches Stable only once it has been OUT FOR A DAY with every test passing, and
the user can hold a version back. Their other PC stays on Fresh (every release).

THE SOAK IS TIME, NOTHING ELSE. Nothing here can see whether the other PC ran a
version, how long for, or whether it worked. A release is "out for a day" when
its GitHub release has been published for ``SOAK_HOURS``; the user's own
judgment reaches Stable through ``--hold`` (and ``--promote``, their explicit
call to skip the wait).

THE RULE (:func:`choose_stable`, pure, tested offline): the NEWEST release that

* is published (not a draft, not a prerelease),
* was published at least ``SOAK_HOURS`` ago,
* is not held (``holds.txt``),
* carries ``latest.yml``, its installer and the installer's blockmap,
* has NOT been replaced: no newer published release of the same MAJOR.MINOR
  line exists (a patch release is how this repo ships a fix — 1.388.0 out for
  a day while its fix 1.388.1 is 19 h old is skipped, and the fix soaks; a
  newer MINOR release does not disqualify it),
* whose commit's **Tests** workflow ended ``success`` (Release publishes only
  after its own gate, but Tests is a separate workflow that has gone red AFTER
  a green Release: v1.398.0, v1.326.0, v1.394.0 …; a cancelled or skipped run
  is not a verdict), and
* is NEWER than the version Stable names now — Stable never moves backwards
  (installed apps would not downgrade to it anyway).

Nothing qualifies → nothing changes, and the log says why for each candidate.
When the Tests verdict of the newest candidate could not be READ, the run ends
without promoting anything older (else a PC on Stable would download the older
one and then, an hour later, the newer one) — exit 5. ``--promote X.Y.Z``
skips the wait for that version only (and, said in its words, the replacement
rule: naming the version is the user's call) and still refuses a held,
unpublished, Tests-failed or not-newer one (exit 3). A promoted release whose
``latest.yml`` cannot be used publishes nothing (exit 4). Exits 3–5 still write
the hold edits; exit 2 (a bad input) writes nothing.

THE BRANCH (``updates-stable``, an orphan written only by
``.github/workflows/stable.yml``) holds ``latest.yml`` (the promoted version's
manifest with ABSOLUTE installer URLs — the SAME line rewrite the Release job
uses, imported from ``publish_update_manifest``, never copied), ``holds.txt``
(one version per line), ``promoted.json`` (the history) and ``README.md``.

Network calls live in :class:`GitHub` (``gh api`` / ``gh release download``),
so everything that decides is testable without a network. Stdlib only, like
``publish_update_manifest.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))
from publish_update_manifest import _URL_LINE, _VERSION_LINE, rewrite  # noqa: E402 — the ONE rewrite

#: How long a release must have been published before Stable takes it.
SOAK_HOURS = 24
#: The branch this script's output is committed to (stable.yml pushes it).
BRANCH = "updates-stable"
#: The workflow whose verdict on a release's commit decides.
TESTS_WORKFLOW = "tests.yml"
#: How many promotions ``promoted.json`` keeps.
HISTORY_MAX = 500
#: Exit codes. 2: a bad input, nothing written. 3–5: the hold edits are still
#: written and committed, then the workflow run ends red so the user sees it.
EXIT_BAD_INPUT = 2
#: A requested ``--promote`` was refused.
EXIT_PROMOTE_REFUSED = 3
#: The chosen release's ``latest.yml`` could not be used; nothing published.
EXIT_MANIFEST_REFUSED = 4
#: The newest candidate's Tests verdict could not be read; nothing older
#: promoted this run.
EXIT_TESTS_UNREAD = 5
#: Run conclusions that are not a verdict on the code (a re-run someone
#: cancelled must not keep a green release off Stable forever).
IGNORED_CONCLUSIONS = frozenset({"cancelled", "skipped"})

_VERSION_RX = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")

README = """# updates-stable

Machine-read by the installed Iron Jarvis desktop app when its update channel is
**Stable** (Settings › System › Updates). `latest.yml` names the version Stable
offers and its installer (absolute URLs on the GitHub release).

Written only by the **Stable channel** workflow (`.github/workflows/stable.yml`),
every hour. A release reaches Stable once it has been published for a day, no
newer patch release of it (same first two numbers) has come out, its commit's
Tests workflow passed, and it is not held back. Stable never moves to an older
version.

* `holds.txt`: versions kept off Stable, one per line. Add or remove one with the
  workflow's `hold` / `unhold` inputs (Actions › Stable channel › Run workflow).
* `promoted.json`: every promotion, newest last.

The wait is time only: nothing here can see whether another PC ran a version.
Do not edit `latest.yml` by hand.
"""


# --------------------------------------------------------------------------- #
# versions
# --------------------------------------------------------------------------- #


def parse_version(text: Any) -> tuple[int, int, int] | None:
    """``"1.404.0"`` / ``"v1.404.0"`` → ``(1, 404, 0)``; anything else None."""
    m = _VERSION_RX.match(str(text or "").strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def clean_version(text: Any) -> str:
    """A version as the files and the app write it (``1.404.0``), or raises."""
    v = parse_version(text)
    if v is None:
        raise ValueError(f"a version must look like 1.404.0, got {str(text or '').strip()!r}")
    return "%d.%d.%d" % v


def required_assets(version: str) -> tuple[str, ...]:
    """The files an installed app needs from a release: the manifest, the
    installer and its blockmap (differential download)."""
    return ("latest.yml", f"Iron-Jarvis-Setup-{version}.exe", f"Iron-Jarvis-Setup-{version}.exe.blockmap")


# --------------------------------------------------------------------------- #
# the facts, normalised
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Release:
    version: str
    tag: str
    draft: bool = False
    prerelease: bool = False
    published_at: datetime | None = None
    assets: frozenset[str] = frozenset()

    @property
    def key(self) -> tuple[int, int, int]:
        return parse_version(self.version) or (0, 0, 0)


def _when(text: Any) -> datetime | None:
    s = str(text or "").strip()
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def release_from_api(raw: Mapping[str, Any]) -> Release | None:
    """One entry of ``GET /repos/{r}/releases`` → a :class:`Release`, or None
    for a tag that is not a version. An asset counts only once its upload
    finished (``state == "uploaded"``; a missing state is taken as uploaded)."""
    tag = str(raw.get("tag_name") or "")
    v = parse_version(tag)
    if v is None:
        return None
    names = frozenset(
        str(a.get("name") or "")
        for a in raw.get("assets") or []
        if isinstance(a, Mapping) and str(a.get("state") or "uploaded") == "uploaded"
    )
    return Release(
        version="%d.%d.%d" % v,
        tag=tag,
        draft=bool(raw.get("draft")),
        prerelease=bool(raw.get("prerelease")),
        published_at=_when(raw.get("published_at")),
        assets=names,
    )


def _newest(runs: list[Mapping[str, Any]]) -> Mapping[str, Any]:
    return max(
        runs,
        key=lambda r: (str(r.get("created_at") or ""), int(r.get("run_number") or 0), int(r.get("id") or 0)),
    )


def tests_verdict(runs: Iterable[Mapping[str, Any]]) -> str:
    """The Tests verdict for one commit, from its workflow runs (``GET
    …/actions/workflows/tests.yml/runs?head_sha=``): the NEWEST COMPLETED
    run's conclusion (a re-run that passed after a flake is that run's latest
    attempt; a later run that failed overrules an earlier pass), leaving out
    runs that ended ``cancelled`` or ``skipped`` (no verdict on the code);
    ``running`` when nothing has finished with a verdict yet but a run is still
    going, ``missing`` when there is no run at all."""
    runs = [r for r in runs if isinstance(r, Mapping)]
    if not runs:
        return "missing"
    done = [r for r in runs if str(r.get("status") or "") == "completed"]
    judged = [r for r in done if str(r.get("conclusion") or "") not in IGNORED_CONCLUSIONS]
    if judged:
        return str(_newest(judged).get("conclusion") or "unknown")
    if len(done) < len(runs):
        return "running"
    return str(_newest(done).get("conclusion") or "unknown")


def _tests_words(verdict: Any) -> str:
    if verdict is None:
        return "its Tests result was not read"
    v = str(verdict)
    if v == "missing":
        return "no Tests run was found for its commit"
    if v == "running":
        return "its Tests run has not finished"
    return f"its Tests run ended “{v}”"


def _green(verdict: Any) -> bool:
    return verdict is True or verdict == "success"


def _hours(delta: timedelta) -> str:
    """Whole hours, rounded DOWN: 23.9 h out is "23 h of the 24 h", never "24 h"."""
    h = delta.total_seconds() / 3600
    return f"{int(h)} h" if h >= 1 else f"{max(0, int(h * 60))} min"


# --------------------------------------------------------------------------- #
# the decision
# --------------------------------------------------------------------------- #


@dataclass
class Decision:
    #: The version to put on Stable; None = no change.
    version: str | None = None
    #: One line: why this version, or why nothing moved.
    why: str = ""
    #: One line per candidate looked at, newest first.
    notes: list[str] = field(default_factory=list)
    #: A requested ``--promote`` that could not happen, in words ("" = none).
    refused: str = ""
    published_at: str = ""
    #: The newest candidate whose Tests verdict was not read ("" = none):
    #: nothing older was promoted.
    unread: str = ""


def replaced_by(r: Release, releases: Iterable[Release]) -> Release | None:
    """The NEWEST published release of ``r``'s MAJOR.MINOR line that is newer
    than ``r`` — a patch release is how this repo ships a fix — or None. A
    draft or prerelease replaces nothing; a newer MINOR release is another line."""
    newer = [
        x for x in releases
        if x.key[:2] == r.key[:2] and x.key > r.key
        and not x.draft and not x.prerelease and x.published_at is not None
    ]
    return max(newer, key=lambda x: x.key) if newer else None


def _blockers(r: Release, holds: set[str]) -> list[str]:
    """What keeps a release off Stable whatever the clock says."""
    out: list[str] = []
    if r.draft or r.published_at is None:
        out.append("it is not published yet (still a draft)")
    if r.prerelease:
        out.append("it is a prerelease")
    missing = [n for n in required_assets(r.version) if n not in r.assets]
    if missing:
        out.append("its release is missing " + ", ".join(missing))
    if r.version in holds:
        out.append("you held it back")
    return out


def tests_wanted(
    releases: Iterable[Release],
    holds: Iterable[str],
    now: datetime,
    current: str | None,
    soak_hours: float = SOAK_HOURS,
    promote: str = "",
) -> list[str]:
    """The versions whose Tests verdict the decision needs, newest first:
    newer than ``current``, published with every file, not held, not replaced
    by a newer patch, and out for the soak (or the one ``promote`` names).
    Everything else is decided without a Tests read, so the network layer asks
    only for these."""
    rels = sorted(releases, key=lambda r: r.key, reverse=True)
    cur = parse_version(current) if current else None
    hold = {clean_version(h) for h in holds}
    out: list[str] = []
    for r in rels:
        if cur is not None and r.key <= cur:
            break
        if _blockers(r, hold):
            continue
        if promote and r.version == promote:
            out.append(r.version)
            continue
        if replaced_by(r, rels) is not None:
            continue
        if r.published_at is not None and now - r.published_at >= timedelta(hours=soak_hours):
            out.append(r.version)
    return out


def choose_stable(
    releases: Iterable[Release],
    holds: Iterable[str],
    tests_green: Mapping[str, Any],
    now: datetime,
    current: str | None,
    soak_hours: float = SOAK_HOURS,
    promote: str = "",
) -> Decision:
    """THE RULE (module docstring). ``tests_green`` maps a version to its
    Tests verdict (``"success"`` / ``True`` is green; ``"failure"``,
    ``"running"``, ``"missing"``, None … are not). ``current`` is the version
    Stable names now (None = the branch is new). ``promote`` = the user's
    explicit call to put that version on Stable now, skipping the wait."""
    rels = sorted(releases, key=lambda r: r.key, reverse=True)
    hold = {clean_version(h) for h in holds}
    cur = parse_version(current) if current else None
    cur_words = f"v{current}" if cur is not None else "nothing yet"
    soak = timedelta(hours=soak_hours)
    d = Decision()

    if cur is not None and current in hold:
        d.notes.append(
            f"v{current} is on Stable and held back: Stable never moves to an older version, so it "
            "stays until a newer version qualifies."
        )

    if promote:
        want = clean_version(promote)
        r = next((x for x in rels if x.version == want), None)
        if r is None:
            d.refused = f"There is no release v{want}, so it cannot go on Stable."
        elif cur is not None and r.key == cur:
            d.why = f"v{want} is already on Stable; nothing to change."
            return d
        elif cur is not None and r.key < cur:
            d.refused = (
                f"Stable already offers v{current}, which is newer than v{want}. Stable never moves "
                "to an older version (installed apps would not go back to it)."
            )
        else:
            blocks = _blockers(r, hold)
            if blocks:
                held = "you held it back" in blocks
                d.refused = f"v{want} cannot go on Stable: " + "; ".join(blocks) + (
                    ". Unhold it first." if held else "."
                )
            elif not _green(tests_green.get(want)):
                d.refused = f"v{want} cannot go on Stable: {_tests_words(tests_green.get(want))}."
        if d.refused:
            d.why = "Nothing changed: " + d.refused
            return d
        age = now - r.published_at if r.published_at else timedelta(0)
        d.version = r.version
        d.published_at = r.published_at.isoformat() if r.published_at else ""
        # Naming the version is the user's call, so a newer patch of its line
        # does not stop it — but the record says so.
        newer = replaced_by(r, rels)
        d.why = (
            f"v{r.version} put on Stable by request, skipping the day's wait "
            f"(out for {_hours(age)}; its Tests passed"
            + (f"; v{newer.version} had replaced it" if newer is not None else "")
            + ")."
        )
        return d

    for r in rels:
        if cur is not None and r.key <= cur:
            d.notes.append(f"v{r.version} and older: not newer than {cur_words} on Stable.")
            break
        blocks = _blockers(r, hold)
        if blocks:
            d.notes.append(f"v{r.version}: skipped, " + "; ".join(blocks) + ".")
            continue
        newer = replaced_by(r, rels)
        if newer is not None:
            d.notes.append(f"v{r.version}: replaced by v{newer.version} — waiting for that one.")
            continue
        age = now - r.published_at  # type: ignore[operator] — _blockers ruled out None
        if age < soak:
            d.notes.append(
                f"v{r.version}: out for {_hours(age)} of the {soak_hours:g} h it must be out first."
            )
            continue
        verdict = tests_green.get(r.version)
        if verdict is None:
            # Not READ (a failed or missing read) — never green, and nothing
            # older goes on Stable this run: a PC on Stable would otherwise
            # download the older one now and this one an hour later.
            d.unread = r.version
            d.notes.append(f"v{r.version}: {_tests_words(None)}.")
            d.why = (
                f"Nothing changed: the Tests result of v{r.version} could not be read, so nothing "
                "older is put on Stable this run."
            )
            return d
        if not _green(verdict):
            d.notes.append(f"v{r.version}: skipped, {_tests_words(verdict)}.")
            continue
        d.version = r.version
        d.published_at = r.published_at.isoformat() if r.published_at else ""
        d.why = f"v{r.version} has been out for {_hours(age)} and its Tests passed."
        d.notes.append(f"v{r.version}: qualifies.")
        return d
    d.why = f"Nothing qualifies, so Stable stays on {cur_words}."
    return d


# --------------------------------------------------------------------------- #
# the branch's files
# --------------------------------------------------------------------------- #


def read_holds(text: str) -> list[str]:
    """``holds.txt`` → versions, in file order, each once. A line that is not
    a version is ignored (a stray note never blocks a run)."""
    out: list[str] = []
    for line in str(text or "").splitlines():
        v = parse_version(line.split("#", 1)[0])
        if v is not None:
            s = "%d.%d.%d" % v
            if s not in out:
                out.append(s)
    return out


def apply_hold_edits(holds: list[str], hold: str = "", unhold: str = "") -> tuple[list[str], list[str]]:
    """The holds after this run's ``--hold`` / ``--unhold`` (each validated),
    plus one line per edit for the log. Raises ValueError on a bad version or
    on the same version in both."""
    out = list(holds)
    said: list[str] = []
    h = clean_version(hold) if str(hold or "").strip() else ""
    u = clean_version(unhold) if str(unhold or "").strip() else ""
    if h and h == u:
        raise ValueError(f"v{h} cannot be held and unheld in one run")
    if h:
        if h in out:
            said.append(f"v{h} was already held back.")
        else:
            out.append(h)
            said.append(f"v{h} is now held back from Stable.")
    if u:
        if u in out:
            out.remove(u)
            said.append(f"v{u} is no longer held back.")
        else:
            said.append(f"v{u} was not held back.")
    return out, said


def manifest_version(text: str) -> str | None:
    """The ``version:`` line of a ``latest.yml`` (None when absent or not a
    version)."""
    for line in str(text or "").splitlines():
        m = _VERSION_LINE.match(line.rstrip("\r\n"))
        if m:
            v = parse_version(m.group(1).strip("'\""))
            return "%d.%d.%d" % v if v else None
    return None


def stable_manifest(text: str, repo: str, release: Release) -> str:
    """The release's own ``latest.yml`` with absolute URLs — the Release job's
    rewrite, unchanged — after checking every file it names is on the release
    (a manifest pointing at a missing installer must never reach Stable)."""
    out = rewrite(text, repo, release.version)
    for line in out.splitlines():
        m = _URL_LINE.match(line)
        if m:
            name = m.group(2).rsplit("/", 1)[-1]
            if name not in release.assets:
                raise ValueError(f"latest.yml names {name}, which is not on the v{release.version} release")
    return out


def read_history(text: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(text) if str(text or "").strip() else []
    except ValueError:
        return []
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


# --------------------------------------------------------------------------- #
# the network (thin: everything above is decided without it)
# --------------------------------------------------------------------------- #


class GitHub:
    """``gh`` calls, nothing else. ``GH_TOKEN`` comes from the workflow."""

    def __init__(self, repo: str, timeout: float = 60.0) -> None:
        self.repo = repo
        self.timeout = timeout

    def _gh(self, *args: str) -> str:
        res = subprocess.run(
            ["gh", *args], capture_output=True, text=True, encoding="utf-8", timeout=self.timeout,
        )
        if res.returncode != 0:
            raise RuntimeError(f"gh {' '.join(args[:2])} failed: {(res.stderr or res.stdout).strip()[:500]}")
        return res.stdout

    def releases(self, limit: int = 100) -> list[dict[str, Any]]:
        out = json.loads(self._gh("api", f"repos/{self.repo}/releases?per_page={min(limit, 100)}"))
        return out if isinstance(out, list) else []

    def tag_commit(self, tag: str) -> str:
        return self._gh("api", f"repos/{self.repo}/commits/{tag}", "--jq", ".sha").strip()

    def tests_runs(self, sha: str) -> list[dict[str, Any]]:
        out = json.loads(self._gh(
            "api", f"repos/{self.repo}/actions/workflows/{TESTS_WORKFLOW}/runs?head_sha={sha}&per_page=50",
        ))
        runs = out.get("workflow_runs") if isinstance(out, dict) else None
        return runs if isinstance(runs, list) else []

    def manifest(self, tag: str) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            self._gh("release", "download", tag, "--repo", self.repo, "--pattern", "latest.yml", "--dir", tmp)
            return (Path(tmp) / "latest.yml").read_text(encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.write_bytes(text.replace("\r\n", "\n").encode("utf-8"))


def run(
    branch_dir: Path,
    repo: str,
    gh: Any,
    *,
    now: datetime,
    hold: str = "",
    unhold: str = "",
    promote: str = "",
    soak_hours: float = SOAK_HOURS,
    log: Any = print,
) -> tuple[int, str]:
    """One pass: edit holds, read the facts, decide, write the branch's files.
    Returns (exit code, one-line commit message). Writes nothing when the
    inputs are invalid (exit 2); exits 3–5 (module docstring) still write the
    hold edits."""
    try:
        promote = clean_version(promote) if str(promote or "").strip() else ""
        holds_path = branch_dir / "holds.txt"
        holds, said = apply_hold_edits(
            read_holds(holds_path.read_text(encoding="utf-8") if holds_path.exists() else ""), hold, unhold,
        )
    except ValueError as exc:
        log(f"promote_stable: {exc}")
        return EXIT_BAD_INPUT, ""
    for line in said:
        log(line)

    latest_path = branch_dir / "latest.yml"
    current = manifest_version(latest_path.read_text(encoding="utf-8")) if latest_path.exists() else None
    log(f"Stable offers {'v' + current if current else 'nothing yet'}.")

    releases = [r for r in (release_from_api(x) for x in gh.releases()) if r is not None]
    tests: dict[str, str] = {}
    for v in tests_wanted(releases, holds, now, current, soak_hours, promote):
        if promote and v != promote:
            continue  # a promote run decides that one version only
        r = next(x for x in releases if x.version == v)
        try:
            tests[v] = tests_verdict(gh.tests_runs(gh.tag_commit(r.tag)))
        except Exception as exc:  # noqa: BLE001 — a read that failed is "not read", never green
            log(f"v{v}: could not read its Tests result ({str(exc).splitlines()[0][:200]}).")
            break  # the decision stops at this one: nothing older is promoted this run
        if _green(tests[v]) and not promote:
            break  # the newest qualifying one wins; older ones need no read

    decision = choose_stable(releases, holds, tests, now, current, soak_hours, promote)
    for line in decision.notes:
        log("  " + line)
    log(decision.why)

    edits = "; ".join(s.rstrip(".") for s in said)
    message = ""
    code = 0
    if decision.refused:
        code = EXIT_PROMOTE_REFUSED
    elif decision.unread:
        code = EXIT_TESTS_UNREAD
    if decision.version:
        r = next(x for x in releases if x.version == decision.version)
        try:
            text = stable_manifest(gh.manifest(r.tag), repo, r)
        except Exception as exc:  # noqa: BLE001 — never publish a manifest we could not check
            log(f"Nothing changed: the v{r.version} manifest could not be used ({exc}).")
            decision.version = None
            code = EXIT_MANIFEST_REFUSED
        else:
            _write(latest_path, text)
            history_path = branch_dir / "promoted.json"
            history = read_history(history_path.read_text(encoding="utf-8") if history_path.exists() else "")
            history.append({
                "version": r.version,
                "promoted_at": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "published_at": decision.published_at.replace("+00:00", "Z"),
                "why": decision.why,
            })
            _write(history_path, json.dumps(history[-HISTORY_MAX:], indent=2) + "\n")
            message = f"stable: v{r.version}"
    _write(branch_dir / "holds.txt", "".join(f"{v}\n" for v in holds))
    _write(branch_dir / "README.md", README)
    if not message:
        message = f"stable: {edits}" if edits else "stable: no change"
    elif edits:
        message += f" ({edits})"
    return code, message


def main(argv: list[str] | None = None, gh: Any = None, now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", required=True, type=Path, help=f"a checkout of the {BRANCH} branch")
    ap.add_argument("--repo", required=True, help="OWNER/NAME on GitHub")
    ap.add_argument("--hold", default="", help="keep this version off Stable")
    ap.add_argument("--unhold", default="", help="let this version reach Stable again")
    ap.add_argument("--promote", default="", help="put this version on Stable now, skipping the wait")
    ap.add_argument("--soak-hours", type=float, default=SOAK_HOURS)
    ap.add_argument("--message-out", type=Path, default=None, help="write the commit message here")
    args = ap.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo.strip()):
        print(f"promote_stable: repo must be OWNER/NAME, got {args.repo!r}", file=sys.stderr)
        return 2
    args.dir.mkdir(parents=True, exist_ok=True)
    code, message = run(
        args.dir, args.repo.strip(), gh or GitHub(args.repo.strip()),
        now=now or datetime.now(timezone.utc), hold=args.hold, unhold=args.unhold,
        promote=args.promote, soak_hours=args.soak_hours,
    )
    if args.message_out is not None and message:
        args.message_out.write_text(message + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    os.environ.setdefault("GH_PROMPT_DISABLED", "1")
    raise SystemExit(main())

"""What the Linux release does for one version: where its rehearsal starts, and which files go up.

Run by ``.github/workflows/linux-release.yml`` with the runner's own python3
(standard library only), from the WORKFLOW's commit: a run for an older tag
still decides with today's rules.

``plan``   reads EVERY release (``gh api --paginate``) and the version that
           ``updates-linux`` names, and says:
           * ``previous``: the newest published version older than this one
             that carries the Linux update file and the single-file app (the
             update rehearsal starts from it);
           * ``first``: nothing older has them AND Linux was never published
             for a newer version. When nothing is found but ``updates-linux``
             names an older version, Linux HAS been published before, so the
             list was cut short or the files are gone: that fails, because
             nothing is published without the rehearsal;
           * ``state``: none | complete | partial | broken, the Linux files
             this release already has. Only a publishing run acts on it: a
             partial or broken release stops the plan with what to do; a
             complete one builds nothing.
``attach`` decides, per file and in a fixed order (single-file app, package,
           update file LAST): ``upload`` (not there), ``keep`` (there with this
           build's bytes) or ``refuse`` (there with other bytes, or never
           finished uploading). Any refuse stops before anything is uploaded.
           A file is never replaced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

APPIMAGE = "Iron-Jarvis-x86_64.AppImage"
DEB = "Iron-Jarvis-amd64.deb"
UPDATE_FILE = "latest-linux.yml"
# The order files go up in: the update file LAST, so a copy that reads
# GitHub's own list is offered nothing until both files are there.
LINUX_FILES = (APPIMAGE, DEB, UPDATE_FILE)

_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


class PlanError(Exception):
    """A reason to stop: nothing is built or published."""


def parse_version(text: str) -> tuple[int, int, int] | None:
    m = _VERSION.match(str(text or "").strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _must(version: str) -> tuple[int, int, int]:
    v = parse_version(version)
    if v is None:
        raise PlanError(f"'{version}' is not a version like 1.540.0.")
    return v


# --------------------------------------------------------------------------- #
# reading what gh printed
# --------------------------------------------------------------------------- #


def read_releases(text: str) -> list[dict]:
    """Every release in ``text``: one object per line (``--paginate --jq '.[]'``),
    or one array per page back to back (``--paginate`` alone)."""
    out: list[dict] = []
    dec = json.JSONDecoder()
    i, n = 0, len(text)

    def take(value) -> None:
        if isinstance(value, list):
            for item in value:
                take(item)
        elif isinstance(value, dict):
            out.append(value)
        else:
            raise PlanError("The list of releases holds something that is not a release.")

    while True:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            return out
        try:
            value, i = dec.raw_decode(text, i)
        except json.JSONDecodeError as exc:
            raise PlanError(f"The list of releases could not be read ({exc.msg} at {exc.pos}).") from None
        take(value)


def branch_version(text: str) -> str | None:
    """The version ``updates-linux``'s update file names; None when there is no file."""
    if not str(text or "").strip():
        return None
    m = re.search(r"^version:\s*['\"]?(\d+\.\d+\.\d+)['\"]?\s*$", text, re.M)
    if not m:
        raise PlanError("updates-linux has an update file that names no version.")
    return m.group(1)


def _assets(release: dict) -> dict[str, dict]:
    return {str(a.get("name")): a for a in (release.get("assets") or []) if isinstance(a, dict)}


def _uploaded(asset: dict | None) -> bool:
    return bool(asset) and asset.get("state") == "uploaded"


# --------------------------------------------------------------------------- #
# LX2: where the rehearsal starts
# --------------------------------------------------------------------------- #


def previous_linux_version(releases: list[dict], version: str) -> str | None:
    """The newest published (not draft, not prerelease) version older than
    ``version`` that carries the update file and the single-file app, both
    fully uploaded."""
    want = _must(version)
    best: tuple[tuple[int, int, int], str] | None = None
    for r in releases:
        if r.get("draft") or r.get("prerelease"):
            continue
        name = str(r.get("tag_name") or "")
        v = parse_version(name[1:] if name.startswith("v") else name)
        if v is None or v >= want:
            continue
        assets = _assets(r)
        if not (_uploaded(assets.get(UPDATE_FILE)) and _uploaded(assets.get(APPIMAGE))):
            continue
        if best is None or v > best[0]:
            best = (v, "%d.%d.%d" % v)
    return best[1] if best else None


def plan_previous(releases: list[dict], version: str, branch: str | None) -> tuple[str | None, bool]:
    """``(previous, first)``. Raises PlanError when the rehearsal would be
    skipped although Linux was published before."""
    want = _must(version)
    if not any(str(r.get("tag_name")) == f"v{version}" for r in releases):
        raise PlanError(f"The list of releases did not include v{version} itself, so it was cut short. "
                        "Run this again.")
    previous = previous_linux_version(releases, version)
    if previous:
        return previous, False
    if branch is None or _must(branch) >= want:
        return None, True
    raise PlanError(f"No earlier Linux version was found to rehearse from, but updates-linux names {branch}. "
                    "Nothing is published without the rehearsal.")


# --------------------------------------------------------------------------- #
# LX3: what the release already has, and what goes up
# --------------------------------------------------------------------------- #


@dataclass
class State:
    kind: str                                   # none | complete | partial | broken
    present: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    bad: list[str] = field(default_factory=list)


def _linux_looking(name: str) -> bool:
    return name.endswith(".AppImage") or name.endswith(".deb") or name == UPDATE_FILE


def release_state(release: dict) -> State:
    assets = _assets(release)
    present = [n for n in LINUX_FILES if n in assets]
    missing = [n for n in LINUX_FILES if n not in assets]
    bad = [n for n in present if not _uploaded(assets[n])]
    # A Linux file this workflow does not make (an older build's name): never built over.
    bad += sorted(n for n in assets if _linux_looking(n) and n not in LINUX_FILES)
    if bad:
        return State("broken", present, missing, bad)
    if not present:
        return State("none", present, missing)
    if missing:
        return State("partial", present, missing)
    return State("complete", present, missing)


@dataclass
class Step:
    name: str
    action: str                                 # upload | keep | refuse
    reason: str


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def attach_plan(release: dict, built_dir: Path, fetch=None) -> list[Step]:
    """Per Linux file, in upload order: upload, keep or refuse. ``fetch(name)``
    downloads a release file whose checksum GitHub did not give (None: such a
    file is refused, never guessed)."""
    assets = _assets(release)
    plan: list[Step] = []
    for name in LINUX_FILES:
        built = Path(built_dir) / name
        if not built.is_file() or built.stat().st_size == 0:
            raise PlanError(f"This build has no {name}.")
        asset = assets.get(name)
        if asset is None:
            plan.append(Step(name, "upload", "not on the release yet"))
            continue
        if not _uploaded(asset):
            plan.append(Step(name, "refuse", f"on the release but never finished uploading ({asset.get('state')})"))
            continue
        mine = sha256_file(built)
        digest = str(asset.get("digest") or "")
        if digest.startswith("sha256:"):
            theirs = digest[len("sha256:"):].lower()
        elif fetch is not None:
            got = fetch(name)
            theirs = sha256_file(Path(got)) if got else ""
        else:
            theirs = ""
        if not theirs:
            plan.append(Step(name, "refuse", "on the release, and its bytes could not be compared"))
        elif theirs == mine:
            plan.append(Step(name, "keep", "on the release with this build's bytes"))
        else:
            plan.append(Step(name, "refuse", "on the release with other bytes than this build"))
    return plan


# --------------------------------------------------------------------------- #
# the commands
# --------------------------------------------------------------------------- #


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _write_outputs(path: str, values: dict[str, str]) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        for k, v in values.items():
            fh.write(f"{k}={v}\n")


def cmd_plan(a: argparse.Namespace) -> int:
    version = a.version
    attach = a.attach == "true"
    releases = read_releases(_read(a.releases))
    release = json.loads(_read(a.release))
    branch = branch_version(_read(a.branch))
    state = release_state(release)
    if attach and state.kind in ("partial", "broken"):
        if state.kind == "partial":
            have = ", ".join(state.present)
            raise PlanError(
                f"v{version} has only {have} of the three Linux files. Re-run the failed jobs of the Linux run "
                f"that uploaded them (its files are kept 7 days). A new build cannot finish it, because its "
                f"files would not match the ones already there. After 7 days, remove {have} from the release "
                "by hand and run this again.")
        bad = ", ".join(state.bad)
        raise PlanError(
            f"v{version} has a Linux file that never finished uploading or that this workflow does not make: "
            f"{bad}. Remove {bad} from the release by hand, then run this again.")
    if attach and state.kind == "complete":
        if branch is None or _must(branch) < _must(version):
            raise PlanError(
                f"v{version} has all three Linux files but updates-linux still names {branch or 'nothing'}: "
                "re-run the failed jobs of the Linux run that published it.")
        _write_outputs(a.out, {"build": "false", "version": version, "attach": "false",
                               "state": state.kind, "previous": "", "first": "false"})
        print(f"v{version} already has all three Linux files and updates-linux names {branch} - nothing to do")
        return 0
    previous, first = plan_previous(releases, version, branch)
    _write_outputs(a.out, {"build": "true", "version": version, "attach": a.attach, "state": state.kind,
                           "previous": previous or "", "first": "true" if first else "false"})
    start = previous if previous else "nothing, this is the first version with the Linux files"
    print(f"read {len(releases)} releases; updates-linux names {branch or 'nothing yet'}")
    print(f"the Linux release of v{version} (publish={a.attach}; Linux files: {state.kind}; rehearse from: {start})")
    return 0


def _gh_fetch(repo: str, version: str, workdir: Path):
    def fetch(name: str) -> Path | None:
        dest = workdir / "fetched"
        dest.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(["gh", "release", "download", f"v{version}", "--repo", repo,
                               "--pattern", name, "--dir", str(dest)],
                              capture_output=True, text=True, timeout=900)
        got = dest / name
        return got if proc.returncode == 0 and got.is_file() else None
    return fetch


def cmd_attach(a: argparse.Namespace) -> int:
    release = json.loads(_read(a.release))
    with tempfile.TemporaryDirectory() as tmp:
        fetch = _gh_fetch(a.repo, a.version, Path(tmp)) if a.repo else None
        plan = attach_plan(release, Path(a.dir), fetch=fetch)
    refused = [s.name for s in plan if s.action == "refuse"]
    with open(a.out, "w", encoding="utf-8") as fh:
        for s in plan:
            if s.action == "upload" and refused and not a.report:
                continue            # nothing goes up when anything is refused
            fh.write(f"{s.action} {s.name}\n")
    for s in plan:
        print(f"  {s.action:<7} {s.name} ({s.reason})")
    if refused and not a.report:
        names = ", ".join(refused)
        print(f"::error::v{a.version} already has {names}, with other bytes than this build or never fully "
              "uploaded. Nothing was uploaded. "
              "Only the Linux run that built those files can finish this release: re-run its failed jobs "
              "(its files are kept 7 days).")
        return 1
    if all(s.action == "keep" for s in plan):
        print(f"v{a.version} already has all three Linux files from this build - checking the update branch")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--releases", required=True, help="every release, as gh api --paginate printed them")
    p.add_argument("--release", required=True, help="this version's release (gh api .../releases/tags/vX)")
    p.add_argument("--version", required=True)
    p.add_argument("--branch", required=True, help="updates-linux's latest-linux.yml (empty: no branch)")
    p.add_argument("--attach", required=True, choices=("true", "false"))
    p.add_argument("--out", required=True, help="the step's output file ($GITHUB_OUTPUT)")
    t = sub.add_parser("attach")
    t.add_argument("--release", required=True)
    t.add_argument("--dir", required=True, help="the folder with this build's three files")
    t.add_argument("--version", required=True)
    t.add_argument("--repo", default="", help="owner/name: download a file GitHub gave no checksum for")
    t.add_argument("--out", required=True, help="one 'action name' line per file")
    t.add_argument("--report", action="store_true", help="say what publish would do; never fail")
    a = ap.parse_args(argv)
    try:
        return cmd_plan(a) if a.cmd == "plan" else cmd_attach(a)
    except PlanError as exc:
        print(f"::error::{exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

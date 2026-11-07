"""v1.443.0: Antigravity's own model list, read BEFORE any Antigravity session
has run, so Build › Code's model menu offers what ``agy`` itself offers from
the first look. Until now the list came only from a running session
(``configOptions.model``, ``hosted/antigravity_models.json``), so a fresh
install showed "Antigravity's default" alone. The same read tells Iron Jarvis
whether Antigravity is signed in, for the Connections page and the Code view.

HOW (MEASURED on agy 1.5.3, this PC, 2026-11-07, run from a scratch
``USERPROFILE``/``HOME`` and a scratch working folder, NO model call):

* ``agy --log-file <F> models`` prints one model per line on stdout,
  ``<id>\\t<name>``, e.g. ``gemini-3.8-flash-high\\tGemini 3.8 Flash (High)``,
  ``claude-sonnet-4-6\\tClaude Sonnet 4.6 (Thinking)``,
  ``gpt-oss-120b-medium\\tGPT-OSS 120B (Medium)``; "Fetching available
  models..." goes to stderr. Exit 0, about 1-5 s. It asks Google for the
  signed-in account's list (``fetchAvailableModels`` in its log); it starts no
  conversation and writes nothing in the working folder. ``--log-file`` puts
  its log THERE (none under ``~/.gemini/antigravity-cli/log``).
* An ACP session's ``configOptions.model`` lists the SAME models grouped: the
  ids that share a base with a thinking-level suffix (``gemini-3.8-flash-
  {high,medium,low}``) become ONE model ``gemini-3.8-flash`` whose levels are
  the suffixes; an id alone with its suffix (``gpt-oss-120b-medium``) stays
  whole. :func:`parse_models` follows that rule, so a model picked here is a
  value ``session/set_config_option`` takes.
* SIGN-IN LIVES IN WINDOWS' CREDENTIAL STORE ("keyringAuth: loaded token" in
  its log), not in a file under its folder: a scratch home still read the
  user's account. So Iron Jarvis cannot see a sign-in by reading a file, and
  what ``agy models`` does when NOT signed in could not be measured here (a
  sign-in prompt is possible). The read therefore runs only once Antigravity's
  OWN onboarding file says a sign-in finished (``cache/onboarding.json``
  ``onboardingComplete`` / ``consumerOnboardingComplete`` /
  ``enterpriseOnboardingComplete`` — written when the user signed in through
  ``agy``); before that, Antigravity reads as not signed in and nothing runs.
* EVERY ``agy`` run starts Antigravity's own updater ("Spawned background
  update process"). The reading process runs in a kill-on-close Job, which
  would end an update half way, so it gets ``AGY_CLI_DISABLE_AUTO_UPDATE=true``
  (its log: "Auto-update disabled via environment variable"; ``1`` was NOT
  read as on). The user's own ``agy`` runs still update it.

WHEN: in the background (never on a request's path), one read at a time,
when nothing was read yet, the last read is older than :data:`CATALOG_TTL_S`,
or Antigravity's onboarding/sign-in files or its program changed (stats
only). A failed read waits :data:`RETRY_AFTER_FAIL_S`. ``force`` (Connections'
"Check again") skips the age and the wait, never the gate. The suite turns it
off (``tests/conftest.py``); a test that drives it turns it on.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from .events import clip, one_line

log = logging.getLogger(__name__)

#: A read older than this is read again (in the background).
CATALOG_TTL_S = 600.0
#: The whole read may take this long (it answered in 1-5 s).
PROBE_TIMEOUT_S = 45.0
#: After a read Antigravity did not answer, the next one waits this long.
RETRY_AFTER_FAIL_S = 120.0
#: The suite turns the background read off (tests/conftest.py).
AUTO_PROBE = True
#: The most models kept.
MAX_MODELS = 200
#: Antigravity's own switch for its updater (measured: "true" turns it off).
NO_UPDATE_ENV = {"AGY_CLI_DISABLE_AUTO_UPDATE": "true"}
#: The thinking levels an id may end in (Antigravity's ``--effort`` values
#: and the ACP ``thought_level`` options).
LEVELS: tuple[str, ...] = ("minimal", "low", "medium", "high", "xhigh", "max")

_LOCK = threading.Lock()
_RUNNING: set[str] = set()
#: path -> when its last read failed (monotonic seconds).
_FAILED_AT: dict[str, float] = {}

_LINE_RX = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._:/-]{0,119})\t(.{1,200})$")
_LEVEL_LABEL_RX = re.compile(r"\s*\((minimal|low|medium|high|xhigh|max)\)\s*$", re.I)


# --------------------------------------------------------------------------- #
# Antigravity's own files (read-only)
# --------------------------------------------------------------------------- #
def onboarded(home: Path | str | None) -> bool | None:
    """BLOCKING-light (one small read): did Antigravity's own sign-in finish?
    True / False from ``cache/onboarding.json``; None when the file is there
    but unreadable. A missing file reads False (never signed in)."""
    if home is None:
        return None
    path = Path(home) / "cache" / "onboarding.json"
    try:
        if not path.is_file():
            return False
        if path.stat().st_size > 64_000:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return any(data.get(k) is True for k in
               ("onboardingComplete", "consumerOnboardingComplete", "enterpriseOnboardingComplete"))


def watched_files(home: Path | str, argv0: list[str] | None = None) -> list[Path]:
    """The files the list and the sign-in follow: Antigravity's onboarding and
    sign-in-method files, and its program (an update can change the list)."""
    base = Path(home)
    files = [base / "cache" / "onboarding.json", base / "cache" / "acp-auth-state.json"]
    if argv0:
        files.append(Path(argv0[0]))
    return files


def watch_signature(home: Path | str, argv0: list[str] | None = None) -> list[list[Any]]:
    """BLOCKING (a stat each): ``[path, size, mtime_ns]`` rows."""
    out: list[list[Any]] = []
    for path in watched_files(home, argv0):
        try:
            st = path.stat()
            out.append([str(path), int(st.st_size), int(st.st_mtime_ns)])
        except OSError:
            out.append([str(path), -1, 0])
    return out


# --------------------------------------------------------------------------- #
# The kept read
# --------------------------------------------------------------------------- #
def read_doc(path: Path | None) -> dict[str, Any]:
    """BLOCKING-light: the kept read (``{}`` when none or unreadable)."""
    if path is None:
        return {}
    try:
        if path.is_file() and path.stat().st_size < 2_000_000:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        pass
    return {}


def cached_rows(path: Path | None) -> list[dict[str, Any]]:
    """The models the last read kept, as the composer's rows."""
    out: list[dict[str, Any]] = []
    for r in read_doc(path).get("models") or []:
        if not (isinstance(r, dict) and isinstance(r.get("id"), str) and r["id"]):
            continue
        row: dict[str, Any] = {"id": clip(r["id"], 120), "label": clip(r.get("label") or r["id"], 120)}
        if isinstance(r.get("efforts"), list):
            row["efforts"] = [str(e) for e in r["efforts"] if isinstance(e, str)][:12]
        out.append(row)
        if len(out) >= MAX_MODELS:
            break
    return out


def cached_default(path: Path | None) -> str:
    """The model Antigravity listed first (the one a new session starts on)."""
    return clip(str(read_doc(path).get("default") or ""), 120)


def sign_in_state(path: Path | None, home: Path | str | None) -> bool | None:
    """Did the last read say Antigravity is signed in? True when it listed
    models, False when its own words said sign-in, None when not known — and
    None before Antigravity's own sign-in finished (nothing was read then).
    Never False from the onboarding file alone: that file is a hint, and a
    False here greys Antigravity out in the Code view."""
    if onboarded(home) is False:
        return None
    said = read_doc(path).get("signed_in")
    if said is True or said is False:
        return said
    return None


def is_stale(doc: dict[str, Any], *, now: float | None = None, watch: list[list[Any]] | None = None) -> bool:
    at = doc.get("fetched_at")
    if not isinstance(at, (int, float)) or isinstance(at, bool):
        return True
    if (now if now is not None else time.time()) - float(at) > CATALOG_TTL_S:
        return True
    if watch is not None and doc.get("watch") != watch:
        return True
    return False


def reading(path: Path) -> bool:
    """Is a background read of ``path`` under way?"""
    with _LOCK:
        return str(path) in _RUNNING


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def _split_level(mid: str) -> tuple[str, str]:
    for lvl in LEVELS:
        if mid.endswith("-" + lvl) and len(mid) > len(lvl) + 1:
            return mid[: -(len(lvl) + 1)], lvl
    return mid, ""


def parse_models(text: str) -> list[dict[str, Any]]:
    """``agy models`` stdout -> the composer's rows ``{id, label, efforts}``,
    grouped the way Antigravity's own ``configOptions.model`` groups them: ids
    that share a base AND each end in a thinking level become one base model
    whose levels are those suffixes (low to high); an id alone with its suffix
    stays whole. Lines that are not ``<id>\\t<name>`` are left out."""
    listed: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in str(text or "").splitlines():
        m = _LINE_RX.match(line.rstrip("\r"))
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        listed.append((m.group(1), m.group(2).strip()))
    by_base: dict[str, list[tuple[str, str, str]]] = {}
    for mid, name in listed:
        base, lvl = _split_level(mid)
        if lvl:
            by_base.setdefault(base, []).append((mid, lvl, name))
    grouped = {b for b, ids in by_base.items() if len(ids) >= 2}
    rows: list[dict[str, Any]] = []
    done: set[str] = set()
    for mid, name in listed:
        base, lvl = _split_level(mid)
        if lvl and base in grouped:
            if base in done:
                continue
            done.add(base)
            levels = [x for x in LEVELS if any(lv == x for _, lv, _ in by_base[base])]
            label = _LEVEL_LABEL_RX.sub("", name) or base
            rows.append({"id": clip(base, 120), "label": clip(label, 120), "efforts": levels})
        else:
            rows.append({"id": clip(mid, 120), "label": clip(name or mid, 120), "efforts": []})
        if len(rows) >= MAX_MODELS:
            break
    return rows


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
def one_line_masked(text: str) -> str:
    """Antigravity's own words, on one line, with anything shaped like a
    secret masked BEFORE the cut."""
    from ..providers.failure_words import mask_failure_text

    try:
        masked = mask_failure_text(str(text or ""))
    except Exception:  # noqa: BLE001 — never show unmasked words
        return ""
    return one_line(masked, 300)


def probe(argv0: list[str], env: dict[str, str], cwd: Path, log_file: Path,
          timeout: float = PROBE_TIMEOUT_S) -> tuple[list[dict[str, Any]] | None, str, str]:
    """BLOCKING: one ``agy --log-file F models`` run through ``core.procs.run``
    (its own kill-on-close Job; the tree killed at the deadline). Returns
    ``(rows, default, said)``: rows None when it listed nothing; ``said`` is
    its own words when it did not (one line, masked)."""
    from ..core import procs

    try:
        done = procs.run([*argv0, "--log-file", str(log_file), "models"], timeout=timeout,
                         cwd=str(cwd), env=env)
    except Exception as exc:  # noqa: BLE001 — Windows could not start it, or it timed out
        log.debug("Antigravity's model list could not be read", exc_info=True)
        return None, "", one_line_masked(str(exc))
    out, err = done.stdout, done.stderr
    text = out.decode("utf-8", "replace") if isinstance(out, (bytes, bytearray)) else str(out or "")
    etext = err.decode("utf-8", "replace") if isinstance(err, (bytes, bytearray)) else str(err or "")
    rows = parse_models(text) if getattr(done, "returncode", 1) == 0 else []
    if rows:
        first = next((ln for ln in text.splitlines() if _LINE_RX.match(ln.rstrip("\r"))), "")
        default = _split_level(first.split("\t", 1)[0])[0] if first else ""
        if default and not any(r["id"] == default for r in rows):
            default = first.split("\t", 1)[0]
        return rows, default, ""
    said = "\n".join(x for x in (etext.strip(), text.strip()) if x)
    said = "\n".join(ln for ln in said.splitlines() if "Fetching available models" not in ln)
    return None, "", one_line_masked(said)


def probe_env(base: dict[str, str]) -> dict[str, str]:
    """The reading process's environment: the user's own, with Antigravity's
    updater off for this run (it would be killed with the Job)."""
    env = dict(base)
    env.update(NO_UPDATE_ENV)
    return env


def read_now(path: Path, home: Path, argv0: list[str], agy_home: Path) -> bool:
    """BLOCKING: read Antigravity's list now and keep it. False when it did
    not answer (what was kept stays; the sign-in verdict follows its words)."""
    from . import antigravity as A
    from .process import build_env

    cwd = home / "hosted" / "agy-probe"
    cwd.mkdir(parents=True, exist_ok=True)
    log_file = home / "hosted" / "agy-probe.log"
    A.trim_log(log_file)
    rows, default, said = probe(argv0, probe_env(build_env(None)), cwd, log_file)
    watch = watch_signature(agy_home, argv0)
    doc = read_doc(path)
    if rows is None:
        signed: bool | None = False if A._SIGN_IN_RX.search(said or "") else None
        if signed is None and not said:
            signed = doc.get("signed_in") if isinstance(doc.get("signed_in"), bool) else None
        save(path, {**doc, "signed_in": signed, "said": said, "failed_at": time.time()},
             fetched=False, watch=watch)
        return False
    save(path, {"models": rows[:MAX_MODELS], "default": default, "signed_in": True, "said": ""},
         fetched=True, watch=watch)
    return True


def save(path: Path, doc: dict[str, Any], *, fetched: bool, watch: list[list[Any]],
         now: float | None = None) -> None:
    """BLOCKING: keep a read (temp + replace). A failed read keeps the last
    list and its time (the age rule still re-reads it later)."""
    out = dict(doc)
    if fetched:
        out["fetched_at"] = now if now is not None else time.time()
    out["watch"] = watch
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(json.dumps(out, ensure_ascii=False).encode("utf-8"))
        os.replace(tmp, path)
    except OSError:
        log.debug("could not keep Antigravity's model list", exc_info=True)


def refresh_soon(path: Path, home: Path, argv0: list[str], agy_home: Path, *, force: bool = False) -> bool:
    """BLOCKING-light (stats and one small read): start ONE background read
    when Antigravity's sign-in finished and the kept list is missing, old or
    about changed files (``force``: whatever its age). True when a read is
    under way after this call."""
    if not AUTO_PROBE:
        return False
    key = str(path)
    with _LOCK:
        if key in _RUNNING:
            return True
        failed = _FAILED_AT.get(key)
    if onboarded(agy_home) is not True:
        return False
    if not force:
        if failed is not None and time.monotonic() - failed < RETRY_AFTER_FAIL_S:
            return False
        if not is_stale(read_doc(path), watch=watch_signature(agy_home, argv0)):
            return False
    with _LOCK:
        if key in _RUNNING:
            return True
        _RUNNING.add(key)
    threading.Thread(target=_refresh, args=(path, home, list(argv0), agy_home), name="antigravity-catalog",
                     daemon=True).start()
    return True


def _refresh(path: Path, home: Path, argv0: list[str], agy_home: Path) -> None:
    ok = False
    try:
        ok = read_now(path, home, argv0, agy_home)
    except Exception:  # noqa: BLE001 — a background read never raises anywhere
        log.debug("could not read Antigravity's model list", exc_info=True)
    finally:
        with _LOCK:
            if ok:
                _FAILED_AT.pop(str(path), None)
            else:
                _FAILED_AT[str(path)] = time.monotonic()
            _RUNNING.discard(str(path))


def merged(read: list[dict[str, Any]], listed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The menu's rows: what a running session listed (its labels and levels
    are Antigravity's live answer) first in the read's order, then models only
    one of them knows."""
    if not read:
        return list(listed)
    by_id = {str(r.get("id")): r for r in listed if isinstance(r, dict)}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in read:
        rid = str(r.get("id") or "")
        if not rid or rid in seen:
            continue
        seen.add(rid)
        other = by_id.get(rid)
        row = dict(other) if other is not None else dict(r)
        if other is not None and not other.get("efforts") and r.get("efforts"):
            row["efforts"] = list(r["efforts"])
        out.append(row)
    for r in listed:
        rid = str(r.get("id") or "")
        if rid and rid not in seen:
            seen.add(rid)
            out.append(dict(r))
    return out[:MAX_MODELS]

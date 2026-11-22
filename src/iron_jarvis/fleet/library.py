"""The model library: browse models, see what fits this PC, download one
(next leaps 43-a, R1.7; idea 43, "A model library inside the app").

A tab on Model servers (``/fleet?view=models``), in LM Studio's style: a list
with each model's name, what it is good at, its download size, the memory it
needs and a mark against this PC ("Fits this PC (64 GB)", "Tight", "Too
big"). Settled choices:

* **D-43.1** — the list ships with the app (``library_catalog.json``, built
  from ``docs/LOCAL-MODELS.md``'s ladder by memory size), plus a Hugging Face
  search ONLY when the user types in the search box (:func:`hf_search`). The
  search sends only the typed words and the fixed filter for files a local
  model server can run; no key, no name, no address.
* **D-43.2** — a download that would leave less than :data:`FREE_FLOOR` of the
  drive free is refused in words before it starts (:func:`floor_problem`).

What each kind of server can do over the network is the table
:data:`ABILITIES` (from the servers' own documentation); the library shows only
the presses a kind supports. This version downloads into Ollama
(:data:`DOWNLOADS_FOR`). No model server tells another computer how much disk
or memory it has, so a server on another computer reads "not known" and its
fit is never guessed (``fleet/hardware``).

The exact download size is read just before a download from the registry the
server pulls from (Ollama's model manifest, :func:`measured_size`) and marked
``measured``; the list's own size is ``about``. Names and descriptions a server
or Hugging Face sends back are DATA: kept to a model name's own characters
before they are shown or told to a model.

Everything here that touches the network or the disk is BLOCKING: the routes
and tools run it off the event loop.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import hardware as HW

CATALOG_FILE = Path(__file__).with_name("library_catalog.json")

#: D-43.2: a download may not leave less than this share of the drive free.
FREE_FLOOR = 0.10

#: Where a model name's manifest is read (tests point these at a stand-in).
REGISTRY_BASES: dict[str, str] = {
    "registry.ollama.ai": "https://registry.ollama.ai",
    "hf.co": "https://hf.co",
}
#: Hugging Face's model search (D-43.1; tests point it at a stand-in).
HF_API = "https://huggingface.co"
#: The fixed part of the Hugging Face search: files a local server can run,
#: the most downloaded first. Only the user's words are added to it.
HF_FIXED_PARAMS: dict[str, str] = {"filter": "gguf", "sort": "downloads", "direction": "-1", "limit": "20"}
HF_TIMEOUT_S = 8.0
MANIFEST_TIMEOUT_S = 6.0
TAGS_TIMEOUT_S = 3.0
#: The longest search the box sends.
MAX_SEARCH_CHARS = 80
#: A measured size is kept this long (the card, then the press).
MEASURED_KEEP_S = 600.0

MANIFEST_ACCEPT = (
    "application/vnd.docker.distribution.manifest.v2+json, "
    "application/vnd.oci.image.manifest.v1+json"
)

# --- the marks ------------------------------------------------------------- #
FITS = "fits"
TIGHT = "tight"
TOO_BIG = "too_big"
UNKNOWN = "unknown"
FIT_MARKS = (FITS, TIGHT, TOO_BIG, UNKNOWN)

#: A model whose memory is at most this share of the budget fits; up to
#: :data:`TIGHT_SHARE` it is tight (the PC has little left for anything else).
FITS_SHARE = 0.70
TIGHT_SHARE = 0.90
#: The memory rule of ``docs/LOCAL-MODELS.md`` (line 13): a low-bit model
#: needs about 0.5 to 0.6 GB per billion parameters; the upper end is used.
GB_PER_BILLION = 0.6e9
#: Room for the context on top of the weights.
HEADROOM_SHARE = 0.15
HEADROOM_MIN = 1_000_000_000

# --- what each kind of server can do (the servers' own documentation) ------ #
#: ``True`` = yes over the network; ``False`` = no; a word = only in that case
#: ("router": llama.cpp's router mode; "served": the one model it serves;
#: "listed": only what ``/v1/models`` lists, no sizes).
ABILITIES: dict[str, dict[str, Any]] = {
    "ollama": {"installed": True, "loaded": True, "free_disk": False, "free_memory": False,
               "download": True, "load": True, "unload": True, "remove": True},
    "lmstudio": {"installed": True, "loaded": True, "free_disk": False, "free_memory": False,
                 "download": True, "load": True, "unload": True, "remove": False},
    "llamacpp": {"installed": "router", "loaded": "router", "free_disk": False, "free_memory": False,
                 "download": False, "load": "router", "unload": "router", "remove": False},
    "vllm": {"installed": "served", "loaded": "served", "free_disk": False, "free_memory": False,
             "download": False, "load": False, "unload": False, "remove": False},
    "litellm": {"installed": "listed", "loaded": False, "free_disk": False, "free_memory": False,
                "download": False, "load": False, "unload": False, "remove": False},
    "openai-compat": {"installed": "listed", "loaded": False, "free_disk": False, "free_memory": False,
                      "download": False, "load": False, "unload": False, "remove": False},
    "unknown": {"installed": False, "loaded": False, "free_disk": False, "free_memory": False,
                "download": False, "load": False, "unload": False, "remove": False},
}
#: The kinds this version downloads for. LM Studio's download (0.4 and newer)
#: needs its own kind, told apart from a plain OpenAI-compatible server; until
#: then it reads as one and gets no Download press.
DOWNLOADS_FOR: frozenset[str] = frozenset({"ollama"})

#: Why a server gets no Download press, by kind.
NO_DOWNLOAD_WORDS: dict[str, str] = {
    "vllm": "vLLM serves the one model it was started with, so Iron Jarvis cannot download another for it.",
    "litellm": (
        "A LiteLLM gateway passes requests on to other servers, so there is nothing to download into it. "
        "Download on the server behind it."
    ),
    "openai-compat": (
        "Iron Jarvis downloads models into Ollama. This server reads as a plain OpenAI-compatible server "
        "(LM Studio, llama.cpp and others read this way), so download its models in its own app."
    ),
    "unknown": "Iron Jarvis has not told yet what kind of server this is, so it offers no download here.",
}
#: Said beside a server on another computer (no server reports its room).
NOT_KNOWN_WORDS = (
    "This server is on another computer. No model server says how much memory or free space its "
    "computer has, so whether a model fits there is not known."
)
CLOUD_WORDS = (
    "{name} is one of Ollama's cloud models: it runs on Ollama's servers, not on your computer, so the "
    "library does not download it."
)
NO_SERVER_WORDS = (
    "No model server yet. Install Ollama on this PC (ollama.com/download) or add a model server on "
    "Connections to download one of these."
)
BAD_NAME_WORDS = "That is not a model name Iron Jarvis can download. Use a name from the list, like qwen3:8b."
PARTIAL_KEPT_WORDS = "Ollama keeps the part already downloaded; Download again carries on from there."


class LibraryProblem(Exception):
    """A request the library refuses, in words for the person."""

    def __init__(self, words: str, status: int = 409) -> None:
        super().__init__(words)
        self.words = words
        self.status = status


# --------------------------------------------------------------------------- #
# the shipped list
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Entry:
    id: str
    name: str
    good_at: str
    ollama: str
    lmstudio: str = ""
    params_b: float | None = None
    active_b: float | None = None
    context: int | None = None
    tools: bool | None = None
    vision: bool | None = None
    about_bytes: int | None = None
    kind: str = "chat"
    checked: str = ""
    source: str = "list"


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None


@lru_cache(maxsize=1)
def catalog() -> tuple[Entry, ...]:
    """The shipped list, in the ladder's order (smallest first). A broken
    file reads as an empty list (the page says so), never a crash."""
    try:
        raw = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ()
    out: list[Entry] = []
    for row in raw.get("models") or []:
        if not isinstance(row, dict):
            continue
        name = clean_model_name(row.get("ollama"))
        if not name or not str(row.get("id") or ""):
            continue
        out.append(
            Entry(
                id=str(row["id"]),
                name=str(row.get("name") or name),
                good_at=str(row.get("good_at") or ""),
                ollama=name,
                lmstudio=str(row.get("lmstudio") or ""),
                params_b=_num(row.get("params_b")),
                active_b=_num(row.get("active_b")),
                context=int(row["context"]) if isinstance(row.get("context"), int) else None,
                tools=row.get("tools") if isinstance(row.get("tools"), bool) else None,
                vision=row.get("vision") if isinstance(row.get("vision"), bool) else None,
                about_bytes=int(row["about_bytes"]) if isinstance(row.get("about_bytes"), int) else None,
                kind=str(row.get("kind") or "chat"),
                checked=str(row.get("checked") or raw.get("checked") or ""),
            )
        )
    return tuple(out)


def catalog_checked() -> str:
    try:
        return str(json.loads(CATALOG_FILE.read_text(encoding="utf-8")).get("checked") or "")
    except Exception:  # noqa: BLE001
        return ""


def entry_for(model: str) -> Entry | None:
    """The shipped entry for an Ollama name ("qwen3:8b"), its id, or its
    shown name ("Qwen3 8B"); ``None`` when it is not on the list."""
    want = str(model or "").strip()
    if not want:
        return None
    low = want.lower()
    for e in catalog():
        if low in (e.ollama.lower(), e.id.lower(), e.name.lower()) or same_model(e.ollama, want):
            return e
    return None


# --------------------------------------------------------------------------- #
# names
# --------------------------------------------------------------------------- #
_NAME_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,100}){0,2}(?::[A-Za-z0-9][A-Za-z0-9._-]{0,80})?$")


def clean_model_name(raw: Any) -> str:
    """A model name Ollama can pull (``qwen3:8b``, ``user/model:tag``,
    ``hf.co/user/repo:Q4_K_M``), or ``""``. Nothing else ever reaches a
    request: no spaces, no ``..``, no address."""
    name = str(raw or "").strip()
    if not name or len(name) > 200 or ".." in name or "//" in name:
        return ""
    if name.lower().startswith("huggingface.co/"):
        name = "hf.co/" + name[len("huggingface.co/"):]
    if not _NAME_RX.match(name):
        return ""
    parts = name.split(":", 1)[0].split("/")
    if len(parts) == 3 and parts[0].lower() != "hf.co":
        return ""  # another registry's host: not read here
    if parts[0].lower() == "hf.co" and len(parts) != 3:
        return ""
    return name


def is_cloud_name(name: str) -> bool:
    """An Ollama CLOUD model (``gpt-oss:120b-cloud``, ``x:cloud``): it runs on
    Ollama's servers, so the library never downloads one."""
    tag = str(name or "").rsplit(":", 1)[-1].lower() if ":" in str(name or "") else ""
    return tag == "cloud" or tag.endswith("-cloud")


def _split(name: str) -> tuple[str, str]:
    base, _, tag = name.partition(":")
    return base, (tag or "latest")


def same_model(a: str, b: str) -> bool:
    """``llama3.2`` and ``llama3.2:latest`` are one model. Never raises."""
    try:
        return _split(str(a).strip().lower()) == _split(str(b).strip().lower())
    except Exception:  # noqa: BLE001
        return False


def manifest_url(name: str) -> str:
    """Where *name*'s manifest is read (Ollama's registry, or Hugging Face for
    ``hf.co/...``), or ``""`` for a name that is not one of those."""
    clean = clean_model_name(name)
    if not clean:
        return ""
    base, tag = _split(clean)
    parts = base.split("/")
    if parts[0].lower() == "hf.co":
        host, ns, repo = "hf.co", parts[1], parts[2]
    elif len(parts) == 1:
        host, ns, repo = "registry.ollama.ai", "library", parts[0]
    elif len(parts) == 2:
        host, ns, repo = "registry.ollama.ai", parts[0], parts[1]
    else:
        return ""
    root = REGISTRY_BASES.get(host, "")
    return f"{root.rstrip('/')}/v2/{ns}/{repo}/manifests/{tag}" if root else ""


def source_words(name: str) -> str:
    return "Hugging Face" if str(name or "").lower().startswith("hf.co/") else "Ollama's library"


# --------------------------------------------------------------------------- #
# sizes and fit
# --------------------------------------------------------------------------- #
def fmt_bytes(n: Any) -> str:
    """A DOWNLOAD size as the registries and ``ollama list`` print it
    (decimal): ``5.2 GB`` / ``274 MB``; ``""`` for nothing known. The PC's
    memory and the drive's room are :func:`fmt_room` (binary, as Windows
    shows them)."""
    if not isinstance(n, (int, float)) or isinstance(n, bool) or n < 0:
        return ""
    if n >= 1e9:
        gb = n / 1e9
        return f"{gb:.1f} GB" if gb < 10 else f"{gb:.0f} GB"
    if n >= 1e6:
        return f"{n / 1e6:.0f} MB"
    return f"{max(1, round(n / 1e3))} KB"


#: Memory and drive room in words (binary GB, as Windows shows them).
fmt_room = HW.fmt_room


def memory_needed(params_b: float | None = None, size_bytes: int | None = None) -> int | None:
    """About how much memory a model takes to run: its weights (the larger of
    the memory rule and its download size) plus room for the context.
    ``None`` when neither is known."""
    weights = max(
        float(params_b) * GB_PER_BILLION if params_b else 0.0,
        float(size_bytes) if isinstance(size_bytes, (int, float)) and size_bytes > 0 else 0.0,
    )
    if weights <= 0:
        return None
    return int(weights + max(HEADROOM_MIN, weights * HEADROOM_SHARE))


def fit(need: int | None, hw: HW.Hardware) -> tuple[str, str]:
    """``(mark, basis)``: whether a model needing *need* bytes fits on *hw*.
    Graphics memory decides when it is known and the model fits in it; else
    system memory. Anything not known is ``unknown`` (never a guess)."""
    if not need or not hw.this_pc or not hw.total_ram:
        return UNKNOWN, ""
    gpu = hw.gpu_total
    if gpu and need <= gpu * TIGHT_SHARE:
        return FITS, "graphics"
    if need <= hw.total_ram * FITS_SHARE:
        return FITS, "memory"
    if need <= hw.total_ram * TIGHT_SHARE:
        return TIGHT, "memory"
    return TOO_BIG, "memory"


def fit_words(mark: str, hw: HW.Hardware) -> str:
    if mark == FITS:
        return f"Fits this PC ({fmt_room(hw.total_ram)})" if hw.total_ram else "Fits this PC"
    if mark == TIGHT:
        return "Tight"
    if mark == TOO_BIG:
        return "Too big"
    return "Fit not known"


def free_after(size: int | None, hw: HW.Hardware) -> int | None:
    if size is None or hw.model_disk_free is None:
        return None
    return int(hw.model_disk_free) - int(size)


def floor_problem(name: str, size: int | None, hw: HW.Hardware, where: str = "the drive Ollama uses") -> str:
    """D-43.2: the sentence that refuses a download leaving less than a tenth
    of the drive free (``""`` = it may start). A drive whose room is not known
    (a server on another computer) is not refused here; its card says "not
    known"."""
    if hw.model_disk_free is None or not hw.model_disk_total:
        return ""
    if size is None:
        return (
            f"Not downloaded: the size of {name} could not be read, so Iron Jarvis could not check that it "
            f"leaves room on {where}."
        )
    after = int(hw.model_disk_free) - int(size)
    floor = int(hw.model_disk_total * FREE_FLOOR)
    if after >= floor:
        return ""
    if after < 0:
        return (
            f"Not downloaded: {name} needs {fmt_bytes(size)} and {where} has {fmt_room(hw.model_disk_free)} "
            "free. Make room on that drive first."
        )
    return (
        f"Not downloaded: {name} needs {fmt_bytes(size)}, which would leave {fmt_room(after)} free on "
        f"{where}, less than a tenth of it ({fmt_room(floor)}). Make room on that drive first."
    )


# --------------------------------------------------------------------------- #
# the network: measured size, the server's own list, Hugging Face
# --------------------------------------------------------------------------- #
_measured_lock = threading.Lock()
_measured: dict[str, tuple[float, int]] = {}


def measured_size(name: str, *, clock=time.monotonic) -> tuple[int | None, str]:
    """``(bytes, "")`` read from the registry the server pulls *name* from
    (the manifest's config and layers), or ``(None, why)``. One bounded GET
    that carries only the name; kept :data:`MEASURED_KEEP_S`. BLOCKING."""
    url = manifest_url(name)
    if not url:
        return None, "its registry is not one Iron Jarvis reads"
    now = float(clock())
    with _measured_lock:
        hit = _measured.get(url)
        if hit is not None and now - hit[0] < MEASURED_KEEP_S:
            return hit[1], ""
    try:
        from ..core import http as _http

        with _http.client(timeout=MANIFEST_TIMEOUT_S, follow_redirects=True, max_redirects=3) as c:
            r = c.get(url, headers={"Accept": MANIFEST_ACCEPT})
        if r.status_code == 404:
            return None, "the registry does not have that name"
        if r.status_code != 200:
            return None, f"the registry answered {r.status_code}"
        body = r.json()
    except Exception:  # noqa: BLE001 — not known
        return None, "the registry could not be reached"
    total = 0
    seen = False
    for part in [body.get("config")] + list(body.get("layers") or []):
        size = part.get("size") if isinstance(part, dict) else None
        if isinstance(size, int) and not isinstance(size, bool) and size >= 0:
            total += size
            seen = True
    if not seen or total <= 0:
        return None, "the registry's answer had no size"
    with _measured_lock:
        if len(_measured) > 128:
            _measured.clear()
        _measured[url] = (now, total)
    return total, ""


def forget_measured() -> None:
    with _measured_lock:
        _measured.clear()


def server_root(base_url: str) -> str:
    from .probes import normalize_root

    return normalize_root(base_url)


def installed_on(target: "Target", headers: "dict[str, str] | None" = None) -> tuple[list[dict[str, Any]], str]:
    """``(models, why)``: what an Ollama server has installed (``/api/tags``,
    one bounded GET), each ``{id, size_bytes}``; ``([], why)`` when it did not
    answer. Another kind: what the last check of it listed. BLOCKING."""
    if target.kind != "ollama":
        return list(target.listed), ""
    root = server_root(target.base_url)
    if not root:
        return [], "it has no address"
    try:
        from ..core import http as _http

        with _http.client(timeout=TAGS_TIMEOUT_S, follow_redirects=False, trust_env=False) as c:
            r = c.get(f"{root}/api/tags", headers=headers or {})
        if r.status_code != 200:
            return [], f"it answered {r.status_code}"
        rows = (r.json() or {}).get("models") or []
    except Exception:  # noqa: BLE001
        return [], "it did not answer"
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = clean_model_name(row.get("name") or row.get("model"))
        if not name or any(o["id"] == name for o in out):
            continue
        size = row.get("size")
        out.append({"id": name, "size_bytes": size if isinstance(size, int) and size > 0 else None})
    return out[:200], ""


def clean_search_words(raw: Any) -> str:
    """The typed words as they are sent: printable, one space apart, at most
    :data:`MAX_SEARCH_CHARS`."""
    text = "".join(ch if ch.isprintable() else " " for ch in str(raw or ""))
    return " ".join(text.split())[:MAX_SEARCH_CHARS].strip()


_PARAMS_RX = re.compile(r"(?<![A-Za-z0-9.])(\d{1,4}(?:\.\d{1,2})?)[bB](?![A-Za-z0-9])")


def params_from_name(name: str) -> float | None:
    """The parameter count a repository's NAME states ("Qwen3-8B-GGUF" -> 8),
    or ``None``. A mixture's total ("30B-A3B") is the first number."""
    m = _PARAMS_RX.search(str(name or ""))
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    return v if 0 < v <= 2000 else None


def hf_search(words: str) -> tuple[list[Entry], str]:
    """Hugging Face's model search for *words* (D-43.1): ``(entries, why)``.
    Called ONLY when the user typed in the search box; it sends only the
    words and :data:`HF_FIXED_PARAMS` (no key, no name, no address). What
    comes back is data: a repository id outside a model name's characters is
    dropped. BLOCKING."""
    text = clean_search_words(words)
    if len(text) < 2:
        return [], ""
    try:
        from ..core import http as _http

        with _http.client(timeout=HF_TIMEOUT_S, follow_redirects=False) as c:
            r = c.get(f"{HF_API.rstrip('/')}/api/models", params={"search": text, **HF_FIXED_PARAMS})
        if r.status_code != 200:
            return [], f"Hugging Face answered {r.status_code}"
        rows = r.json()
    except Exception:  # noqa: BLE001
        return [], "Hugging Face could not be reached"
    out: list[Entry] = []
    for row in rows if isinstance(rows, list) else []:
        repo = str((row or {}).get("id") or (row or {}).get("modelId") or "") if isinstance(row, dict) else ""
        name = clean_model_name(f"hf.co/{repo}") if repo.count("/") == 1 else ""
        if not name or is_cloud_name(name):
            continue
        downloads = row.get("downloads")
        said = f"{int(downloads):,} downloads" if isinstance(downloads, int) and downloads >= 0 else ""
        out.append(
            Entry(
                id=name,
                name=repo,
                good_at=f"From Hugging Face{', ' + said if said else ''}. Its size is read when you press Download.",
                ollama=name,
                params_b=params_from_name(repo),
                kind="chat",
                source="huggingface",
            )
        )
        if len(out) >= 20:
            break
    return out, ""


# --------------------------------------------------------------------------- #
# the servers a model can be downloaded into
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Target:
    id: str
    label: str
    kind: str
    base_url: str
    this_pc: bool
    node: Any = None
    #: True for Ollama on this PC that is not a saved model server yet.
    seen_only: bool = False
    listed: tuple = ()
    #: Does what this server has reach the model menu? Ollama on this PC
    #: does (the menu finds it); a saved model server only when it is
    #: routable (the menu lists ``fleet.routable_nodes()`` alone). A
    #: finished download says "in the model menu" only when this is True.
    in_menu: bool = False

    @property
    def downloads(self) -> bool:
        return self.kind in DOWNLOADS_FOR

    def where_words(self) -> str:
        if self.kind == "ollama":
            return "the drive Ollama uses" if self.this_pc else f"the drive {self.label} uses"
        return "its drive"


#: The id of Ollama on this PC when it is not a saved server.
THIS_PC_OLLAMA = "ollama"


def _listed_of(sampler: Any, node_id: str) -> tuple:
    try:
        listing = getattr(sampler, "listing", None)
        pairs = list(listing()) if callable(listing) else [(s, True) for s in sampler.snapshots()]
    except Exception:  # noqa: BLE001
        return ()
    for snap, _checked in pairs:
        node = getattr(snap, "node", None)
        if getattr(node, "id", None) != node_id:
            continue
        rows = []
        for m in getattr(snap, "models", []) or []:
            name = clean_model_name(getattr(m, "id", ""))
            if name:
                rows.append({"id": name, "size_bytes": getattr(m, "size_bytes", None)})
        return tuple(rows)
    return ()


def _ollama_seen_here() -> bool:
    """Is Ollama installed or answering on this PC (the cached answer, never
    a wait)? False while the suite keeps the real Ollama out of reach
    (``cli_detect._OLLAMA_PROBE_FORCED_OFF``)."""
    try:
        from ..providers import cli_detect, ollama_seen

        if getattr(cli_detect, "_OLLAMA_PROBE_FORCED_OFF", False):
            return False

        state = ollama_seen.state()
        return bool(state.installed or state.running)
    except Exception:  # noqa: BLE001
        return False


def _this_pc_ollama_address() -> str:
    try:
        from ..providers.cli_detect import OLLAMA_API

        return str(OLLAMA_API)
    except Exception:  # noqa: BLE001
        return "http://127.0.0.1:11434"


def targets(d: Any) -> list[Target]:
    """Every server the library can show: the saved model servers (top
    level, switched on), and Ollama on this PC when it is not saved yet but
    is installed or answering (the starter model downloads there). Ollama
    first, this PC first. Never raises."""
    out: list[Target] = []
    fleet = getattr(d, "fleet", None)
    sampler = getattr(d, "fleet_sampler", None)
    try:
        nodes = list(fleet.nodes()) if fleet is not None else []
    except Exception:  # noqa: BLE001
        nodes = []
    for node in nodes:
        if getattr(node, "parent_id", "") or not getattr(node, "enabled", True):
            continue
        base = str(getattr(node, "base_url", "") or "")
        out.append(
            Target(
                id=str(node.id),
                label=str(getattr(node, "label", "") or node.id),
                kind=str(getattr(node, "kind", "unknown") or "unknown"),
                base_url=base,
                this_pc=HW.on_this_pc(base),
                node=node,
                listed=_listed_of(sampler, str(node.id)),
                in_menu=getattr(node, "routable", False) is True,
            )
        )
    has_here = any(t.kind == "ollama" and t.this_pc for t in out) or any(t.id == THIS_PC_OLLAMA for t in out)
    if not has_here and _ollama_seen_here():
        address = _this_pc_ollama_address()
        if HW.on_this_pc(address):
            out.append(
                Target(THIS_PC_OLLAMA, "Ollama on this PC", "ollama", address, True, seen_only=True, in_menu=True)
            )
    out.sort(key=lambda t: (t.kind != "ollama", not t.this_pc))
    return out


def target_for(d: Any, node_id: str) -> Target:
    want = str(node_id or "").strip()
    for t in targets(d):
        if t.id == want:
            return t
    raise LibraryProblem("That model server is not one of yours.", status=404)


def hardware_for(target: Target, *, force: bool = False) -> HW.Hardware:
    """This PC's numbers for a server on this PC (Ollama's drive for Ollama),
    "not known" for a server on another computer. BLOCKING."""
    if not target.this_pc:
        return HW.NOT_KNOWN
    folder: Any = HW.ollama_models_dir() if target.kind == "ollama" else ""
    return HW.this_pc(folder, force=force)


def auth_headers(d: Any, target: Target) -> dict[str, str]:
    if target.node is None:
        return {}
    try:
        from ..daemon.routes.fleet import fleet_auth_kw

        return dict(fleet_auth_kw(getattr(d, "fleet", None), target.node).get("headers") or {})
    except Exception:  # noqa: BLE001
        return {}


# --------------------------------------------------------------------------- #
# the views
# --------------------------------------------------------------------------- #
def entry_view(e: Entry, hw: HW.Hardware, installed: list[dict[str, Any]], *, downloads: bool) -> dict[str, Any]:
    need = memory_needed(e.params_b, e.about_bytes)
    mark, basis = fit(need, hw)
    have = any(same_model(row["id"], e.ollama) for row in installed)
    return {
        "id": e.id,
        "name": e.name,
        "good_at": e.good_at,
        "model": e.ollama,
        "source": e.source,
        "params_b": e.params_b,
        "active_b": e.active_b,
        "context": e.context,
        "tools": e.tools,
        "vision": e.vision,
        "size_bytes": e.about_bytes,
        "size_from": "about" if e.about_bytes else "",
        "size_words": (f"about {fmt_bytes(e.about_bytes)}" if e.about_bytes else "size shown before it starts"),
        "memory_bytes": need,
        # Memory, in the units the PC's own memory is shown in (fit_words).
        "memory_words": (f"about {fmt_room(need)}" if need else "not known"),
        "fit": mark,
        "fit_basis": basis,
        "fit_words": fit_words(mark, hw),
        "installed": have,
        "can_download": downloads and not have,
        "kind": e.kind,
        "checked": e.checked,
    }


def summary_words(target: Target, hw: HW.Hardware, rows: list[dict[str, Any]]) -> str:
    """The line on top: "This PC has 64 GB of memory and 412 GB free on the
    drive Ollama uses. 7 of the 13 models below fit." Only what was read."""
    if not target.this_pc:
        return NOT_KNOWN_WORDS
    parts: list[str] = []
    if hw.total_ram:
        mem = f"{fmt_room(hw.total_ram)} of memory"
        if hw.gpu_total:
            mem += f", a graphics card with {fmt_room(hw.gpu_total)}"
        parts.append(mem)
    if hw.model_disk_free is not None and target.kind == "ollama":
        parts.append(f"{fmt_room(hw.model_disk_free)} free on {target.where_words()}")
    lead = f"This PC has {' and '.join(parts)}." if parts else "This PC's memory could not be read."
    fits = sum(1 for r in rows if r.get("fit") == FITS)
    known = [r for r in rows if r.get("fit") != UNKNOWN]
    if known:
        lead += f" {fits} of the {len(rows)} models below fit." if fits != 1 else f" 1 of the {len(rows)} models below fits."
    if hw.total_ram and hw.gpus is None:
        lead += " Graphics memory is not known here, so fit is judged by the PC's memory."
    return lead


def download_why(target: Target) -> str:
    if target.downloads:
        return ""
    return NO_DOWNLOAD_WORDS.get(target.kind, NO_DOWNLOAD_WORDS["unknown"])


def target_view(t: Target) -> dict[str, Any]:
    return {
        "id": t.id,
        "label": t.label,
        "kind": t.kind,
        "this_pc": t.this_pc,
        "seen_only": t.seen_only,
        "in_menu": t.in_menu,
        "downloads": t.downloads,
        "abilities": dict(ABILITIES.get(t.kind, ABILITIES["unknown"])),
        "download_why": download_why(t),
    }


def library_view(d: Any, node_id: str = "", q: str = "") -> dict[str, Any]:
    """``GET /fleet/library``: the servers, the chosen one's hardware, the
    shipped list marked against it, what it has installed, and (only when
    *q* has words) Hugging Face's answer. BLOCKING."""
    all_targets = targets(d)
    view: dict[str, Any] = {
        "servers": [target_view(t) for t in all_targets],
        "server": None,
        "hardware": None,
        "summary": "",
        "models": [],
        "installed": [],
        "installed_why": "",
        "search": {"words": clean_search_words(q), "models": [], "why": ""},
        "checked": catalog_checked(),
        "free_floor": FREE_FLOOR,
    }
    if not all_targets:
        # Browse before installing anything: the list marked against this
        # PC's memory, with no Download (there is nowhere to download to).
        here = Target("", "this PC", "unknown", "", True)
        hw = hardware_for(here)
        rows = [entry_view(e, hw, [], downloads=False) for e in catalog()]
        view.update(
            hardware=hw.view(),
            models=rows,
            summary=f"{NO_SERVER_WORDS} {summary_words(here, hw, rows)}",
        )
        return view
    want = str(node_id or "").strip()
    target = next((t for t in all_targets if t.id == want), None) if want else None
    if target is None:
        target = all_targets[0]
    hw = hardware_for(target)
    installed, why = installed_on(target, auth_headers(d, target))
    rows = [entry_view(e, hw, installed, downloads=target.downloads) for e in catalog()]
    view.update(
        server=target_view(target),
        hardware=hw.view(),
        summary=summary_words(target, hw, rows),
        models=rows,
        installed=installed,
        installed_why=why,
    )
    words = view["search"]["words"]
    if len(words) >= 2:
        found, swhy = hf_search(words)
        view["search"] = {
            "words": words,
            "models": [entry_view(e, hw, installed, downloads=target.downloads) for e in found],
            "why": swhy,
        }
    return view


def preview(d: Any, node_id: str, model: str) -> dict[str, Any]:
    """What a download of *model* into *node_id* would be, BEFORE it starts:
    its name, source, exact size (``measured``) or the list's (``about``),
    the free space after, and the refusal sentence when it may not start.
    BLOCKING (one manifest read)."""
    target = target_for(d, node_id)
    name = clean_model_name(model)
    entry = entry_for(model)
    if entry is not None:
        name = entry.ollama
    if not name:
        raise LibraryProblem(BAD_NAME_WORDS, status=400)
    if is_cloud_name(name):
        raise LibraryProblem(CLOUD_WORDS.format(name=name))
    if not target.downloads:
        raise LibraryProblem(download_why(target))
    size, why = measured_size(name)
    size_from = "measured" if size is not None else ""
    if size is None and entry is not None and entry.about_bytes:
        size, size_from = entry.about_bytes, "about"
    hw = hardware_for(target, force=True)
    after = free_after(size, hw)
    refusal = floor_problem(name, size, hw, target.where_words())
    shown = entry.name if entry is not None else name
    if size is None:
        size_words = "size not known"
    else:
        size_words = fmt_bytes(size) if size_from == "measured" else f"about {fmt_bytes(size)}"
    if after is not None:
        after_words = f"{fmt_room(max(after, 0))} free after"
    elif not target.this_pc:
        after_words = "free space after: not known (this server does not report it)"
    else:
        after_words = "free space after: not known"
    return {
        "server": target_view(target),
        "model": name,
        "name": shown,
        "source": source_words(name),
        "size_bytes": size,
        "size_from": size_from,
        "size_why": why if size_from != "measured" else "",
        "size_words": size_words,
        "free_bytes": hw.model_disk_free,
        "free_after": after,
        "free_after_words": after_words,
        "model_dir": hw.model_dir,
        "refusal": refusal,
        "can_start": not refusal,
        "partial_words": PARTIAL_KEPT_WORDS,
        "card_words": (
            f"Downloads {shown} ({name}) from {source_words(name)} into {target.label}: {size_words}, "
            f"{after_words}."
        ),
    }

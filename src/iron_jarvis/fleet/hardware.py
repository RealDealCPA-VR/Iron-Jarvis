"""What this PC has for local models: memory, graphics memory, and room on the
drive a model server keeps its models on (next leaps 43-a, the model library).

The library on Model servers › Models marks each model "Fits this PC",
"Tight" or "Too big" and refuses a download that would leave the drive
nearly full (D-43.2). Both are judged here, from what THIS computer can read
about itself:

* **memory**: ``psutil.virtual_memory`` (total and free);
* **graphics memory**: ``nvidia-smi`` through ``core/procs`` (bounded at
  :data:`NVIDIA_SMI_TIMEOUT_S`, killed as a tree). No NVIDIA card, no
  ``nvidia-smi``, or an answer it cannot read is ``None``: "not known", and the
  fit is then judged by system memory and says so. No WMI
  (``docs/rules/daemon.md``: a native call that can wait inside Windows never
  runs in the daemon);
* **the model drive**: ``shutil.disk_usage`` on the folder the server keeps
  models in (Ollama's ``OLLAMA_MODELS``, else its own default folder), walking
  up to the nearest folder that exists (Ollama makes its folder on the first
  download).

A number that could not be read is ``None``, never ``0`` (``fleet/models``).
No model server tells another computer how much memory or disk it has, so this
module only ever speaks for THIS PC: a server elsewhere reads "not known"
(``fleet/library``).

:func:`this_pc` is BLOCKING (a child process, a disk read): call it off the
event loop. Its answer is kept :data:`CACHE_S` seconds per folder, so a page
opened twice asks ``nvidia-smi`` once.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

#: How long one reading of this PC is kept.
CACHE_S = 60.0
#: The longest ``nvidia-smi`` may take (a hung driver is killed as a tree).
NVIDIA_SMI_TIMEOUT_S = 3.0
#: The question asked of ``nvidia-smi``: one line per card, MiB, no header.
NVIDIA_SMI_ARGS: tuple[str, ...] = (
    "--query-gpu=name,memory.total,memory.free",
    "--format=csv,noheader,nounits",
)
_MIB = 1024 * 1024
#: The suite turns the ``nvidia-smi`` read off (graphics memory then reads
#: "not known", exactly what a PC without an NVIDIA card sees).
GPU_READ_FORCED_OFF = os.environ.get("IRONJARVIS_GPU_READ", "").strip().lower() in ("0", "off", "false", "no")
#: The hosts that mean "this PC".
_THIS_PC_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


@dataclass(frozen=True)
class Gpu:
    """One graphics card as ``nvidia-smi`` reported it (bytes)."""

    name: str
    total: int | None = None
    free: int | None = None


@dataclass(frozen=True)
class Hardware:
    """What this PC has. ``None`` everywhere means "not known"."""

    total_ram: int | None = None
    free_ram: int | None = None
    #: ``None`` = graphics memory is not known (no NVIDIA card's answer);
    #: an empty tuple is never produced.
    gpus: tuple[Gpu, ...] | None = None
    #: The folder the server keeps its models in ("" = not known).
    model_dir: str = ""
    model_disk_free: int | None = None
    model_disk_total: int | None = None
    #: True when these numbers are THIS PC's (False: another computer's server,
    #: whose numbers no server reports).
    this_pc: bool = True
    read_at: float = field(default=0.0, compare=False)

    @property
    def gpu_total(self) -> int | None:
        if not self.gpus:
            return None
        totals = [g.total for g in self.gpus if isinstance(g.total, int) and g.total > 0]
        return max(totals) if totals else None

    def view(self) -> dict[str, Any]:
        return {
            "this_pc": self.this_pc,
            "total_ram": self.total_ram,
            "free_ram": self.free_ram,
            "gpus": None
            if self.gpus is None
            else [{"name": g.name, "total": g.total, "free": g.free} for g in self.gpus],
            "model_dir": self.model_dir,
            "model_disk_free": self.model_disk_free,
            "model_disk_total": self.model_disk_total,
        }


#: What a server on another computer reads as: nothing known.
NOT_KNOWN = Hardware(this_pc=False)


def on_this_pc(url: str) -> bool:
    """Is *url* a server on THIS computer (loopback)? Never raises."""
    try:
        raw = str(url or "").strip()
        if "://" not in raw:
            raw = "http://" + raw
        host = (urlsplit(raw).hostname or "").strip().lower().rstrip(".")
    except Exception:  # noqa: BLE001
        return False
    if not host:
        return False
    return host in _THIS_PC_HOSTS or host.startswith("127.")


def ollama_models_dir(env: "dict[str, str] | None" = None) -> Path:
    """The folder Ollama keeps its models in on this PC: ``OLLAMA_MODELS`` when
    set, else Ollama's own default (``~/.ollama/models``, the same on Windows,
    macOS and Linux for a user install)."""
    source = os.environ if env is None else env
    raw = str(source.get("OLLAMA_MODELS") or "").strip()
    if raw:
        return Path(os.path.expandvars(os.path.expanduser(raw)))
    return Path.home() / ".ollama" / "models"


def disk_of(path: "str | Path | None") -> tuple[int | None, int | None]:
    """``(free, total)`` bytes of the drive *path* is on, walking up to the
    nearest folder that exists; ``(None, None)`` when nothing can be read.
    A network spelling (``\\\\server\\share``) is not read. Never raises."""
    if not path:
        return None, None
    text = str(path)
    if text.startswith("\\\\") or text.startswith("//"):
        return None, None
    try:
        here = Path(text)
        for _ in range(64):
            if here.exists():
                usage = shutil.disk_usage(str(here))
                return int(usage.free), int(usage.total)
            parent = here.parent
            if parent == here:
                break
            here = parent
    except Exception:  # noqa: BLE001 — not known
        pass
    return None, None


def read_memory() -> tuple[int | None, int | None]:
    """``(total, free)`` system memory in bytes, or ``(None, None)``."""
    try:
        import psutil

        vm = psutil.virtual_memory()
        return int(vm.total), int(vm.available)
    except Exception:  # noqa: BLE001
        return None, None


def parse_nvidia_smi(text: str) -> tuple[Gpu, ...] | None:
    """The cards in ``nvidia-smi``'s CSV answer (MiB), or ``None`` when no
    line reads as one. A field it cannot read stays ``None``."""
    cards: list[Gpu] = []
    for line in str(text or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3 or not parts[0]:
            continue

        def _mib(value: str) -> int | None:
            try:
                n = float(value)
            except ValueError:
                return None
            return int(n * _MIB) if n > 0 else None

        cards.append(Gpu(name=parts[0][:80], total=_mib(parts[1]), free=_mib(parts[2])))
    return tuple(cards) if cards else None


def nvidia_smi_path() -> str | None:
    """Where ``nvidia-smi`` is, or ``None`` (none here, or the suite keeps it
    off: :data:`GPU_READ_FORCED_OFF`)."""
    if GPU_READ_FORCED_OFF:
        return None
    try:
        return shutil.which("nvidia-smi")
    except Exception:  # noqa: BLE001
        return None


def read_gpus(*, runner: "Callable[..., Any] | None" = None) -> tuple[Gpu, ...] | None:
    """Graphics memory from ``nvidia-smi`` (through ``core/procs``, bounded),
    or ``None`` = not known. Never raises."""
    exe = nvidia_smi_path()
    if not exe:
        return None
    try:
        if runner is None:
            from ..core import procs

            runner = procs.run
        result = runner([exe, *NVIDIA_SMI_ARGS], timeout=NVIDIA_SMI_TIMEOUT_S)
    except Exception:  # noqa: BLE001 — not known
        return None
    if getattr(result, "timed_out", False) or getattr(result, "returncode", 1) != 0:
        return None
    out = getattr(result, "stdout", b"")
    try:
        text = out.decode("utf-8", "replace") if isinstance(out, (bytes, bytearray)) else str(out)
    except Exception:  # noqa: BLE001
        return None
    return parse_nvidia_smi(text)


_lock = threading.Lock()
_cache: dict[str, Hardware] = {}
#: (read at, cards) of the last ``nvidia-smi`` answer.
_gpu_cache: "tuple[float, tuple[Gpu, ...] | None] | None" = None


def gpus(*, force: bool = False, clock: Callable[[], float] = time.monotonic) -> tuple[Gpu, ...] | None:
    """:func:`read_gpus`, kept :data:`CACHE_S` seconds (the Overview's
    health card and the library both ask; ``nvidia-smi`` runs once a
    minute at most). BLOCKING. Never raises."""
    global _gpu_cache
    now = float(clock())
    with _lock:
        hit = _gpu_cache
    if hit is not None and not force and now - hit[0] < CACHE_S:
        return hit[1]
    cards = read_gpus()
    with _lock:
        _gpu_cache = (now, cards)
    return cards


def this_pc(
    model_dir: "str | Path | None" = None,
    *,
    force: bool = False,
    clock: Callable[[], float] = time.monotonic,
) -> Hardware:
    """This PC's memory, graphics memory and the room on *model_dir*'s drive
    (Ollama's folder when not given). BLOCKING; kept :data:`CACHE_S` seconds
    per folder. Never raises."""
    folder = str(model_dir if model_dir is not None else ollama_models_dir())
    now = float(clock())
    with _lock:
        hit = _cache.get(folder)
        if hit is not None and not force and now - hit.read_at < CACHE_S:
            return hit
    total, free = read_memory()
    # A forced read is for the drive (a download is about to start); the
    # graphics card's size does not change, so nvidia-smi keeps its minute.
    cards = gpus(clock=clock)
    disk_free, disk_total = disk_of(folder)
    hw = Hardware(
        total_ram=total,
        free_ram=free,
        gpus=cards,
        model_dir=folder,
        model_disk_free=disk_free,
        model_disk_total=disk_total,
        this_pc=True,
        read_at=now,
    )
    with _lock:
        if len(_cache) > 16:
            _cache.clear()
        _cache[folder] = hw
    return hw


def forget() -> None:
    """Drop every kept reading of the drive (a download finished: the drive
    changed). Graphics memory is kept: it did not."""
    with _lock:
        _cache.clear()

"""The model library from chat (next leaps 43-a): ``model_library`` and
``model_download``.

* ``model_library`` (allow, reads): what fits this PC and what a server has
  installed, from the same view the Model servers › Models tab shows. Never a
  Hugging Face search (D-43.1: that runs only from the page's search box).
* ``model_download`` (ask, EVERY time): THE download call (ideas 16 and 37
  name it too). Its card shows the model's name, where it comes from, its
  exact size (read from the registry before the card) and the free space
  after; a once-only card, whatever the approval setting, and only where a
  person can answer it (``attended_only``). A download that would leave less
  than a tenth of the drive free is refused in words before any request
  reaches the model server (D-43.2). Kept from a low-trust run
  (``core/trust.LOW_TRUST_DENY``). It returns at once with the download's
  card; progress and Cancel ride that card and the Models tab.

Armed by the message's own words (:func:`library_tools_for`, read by
``daemon.chat_turn.settings_ask_tools`` for both lanes; the POST lane keeps
only the reader) and found by the capability lookup.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from ..fleet import library as L
from ..tools.base import Reversibility, RiskClass, SpendCheck, Tool, ToolContext, ToolResult

READER = "model_library"
WRITER = "model_download"
#: The Models tab on Model servers.
HREF = "/fleet?view=models"
PLACE = "Model servers › Models"
NEEDS_THE_APP = "This can only be done while the Iron Jarvis app is running."

#: How many shipped models the reader tells the model about.
MAX_ROWS = 20
SCAN_CHARS = 2000


def _d(platform: Any) -> Any:
    from . import records as R

    return R._d(platform)  # noqa: SLF001 — the records module's own deps lookup


def _pick_server(d: Any, ref: Any) -> L.Target:
    """The server *ref* names (its id, its name on Model servers, "ollama"),
    else the first that can download. Raises ``LibraryProblem`` in words."""
    all_targets = L.targets(d)
    if not all_targets:
        raise L.LibraryProblem(
            "There is no model server to download into yet. Install Ollama on this PC "
            "(ollama.com/download), then ask again.",
            status=404,
        )
    want = " ".join(str(ref or "").split()).lower()
    if want:
        for t in all_targets:
            if want in (t.id.lower(), t.label.lower()):
                return t
        if want in ("ollama", "this pc", "here", "local"):
            for t in all_targets:
                if t.kind == "ollama" and t.this_pc:
                    return t
        names = ", ".join(f"“{t.label}”" for t in all_targets)
        raise L.LibraryProblem(f"“{ref}” is not one of your model servers. They are: {names}.", status=404)
    for t in all_targets:
        if t.downloads:
            return t
    return all_targets[0]


def _rows_text(view: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for row in (view.get("models") or [])[:MAX_ROWS]:
        bits = [f"{row['name']} ({row['model']})", row.get("good_at") or ""]
        bits.append(f"download {row.get('size_words')}")
        bits.append(f"memory {row.get('memory_words')}")
        bits.append(str(row.get("fit_words") or ""))
        if row.get("installed"):
            bits.append("installed")
        lines.append("- " + "; ".join(b for b in bits if b))
    return lines


def _running_lines(d: Any) -> list[str]:
    """The downloads running now, with how far each has got (the same rows
    the card and Models tab show). ``[]`` when none, or when they could not
    be read. BLOCKING."""
    from ..fleet import pulls as P

    try:
        rows = P.manager_for(d).list()
    except Exception:  # noqa: BLE001 — the rest of the answer still stands
        return []
    said: list[str] = []
    for r in rows:
        if r.get("state") != P.DOWNLOADING:
            continue
        how_far = f"{r['percent']}%" if isinstance(r.get("percent"), int) else str(r.get("done_words") or "")
        where = f" into {r['server']}" if r.get("server") else ""
        said.append(f"{r.get('name') or r.get('model')} ({r.get('model')}){where}, {how_far} so far")
    if not said:
        return []
    return [
        "Downloading now (not finished; Cancel is on its card and on "
        f"{PLACE}): " + "; ".join(said[:5]) + "."
    ]


class ModelLibraryTool(Tool):
    name = READER
    description = (
        "The model library on Model servers › Models: the models Iron Jarvis lists for running on the "
        "user's own computer, each with what it is good at, its download size, the memory it needs and "
        "whether it fits this PC (Fits this PC / Tight / Too big / Fit not known), and which ones a model "
        "server already has installed. `model_server` picks a model server by its NAME on Model servers "
        "(default: Ollama on this PC). It also says which downloads are running now and how far each has got. "
        "Reads only; to download one, call model_download with its name."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "model_server": {"type": "string", "description": "A model server's name; empty = Ollama on this PC."},
        },
    }
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ

    def __init__(self, platform: Any) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        d = _d(self.platform)
        if d is None:
            return ToolResult(ok=False, error=NEEDS_THE_APP)
        try:
            target = await asyncio.to_thread(_pick_server, d, (args or {}).get("model_server"))
            view = await asyncio.to_thread(L.library_view, d, target.id, "")
        except L.LibraryProblem as exc:
            return ToolResult(ok=False, error=exc.words, data={"door": HREF})
        except Exception:  # noqa: BLE001 — said in words, never a traceback
            return ToolResult(ok=False, error="The model library could not be read just now.", data={"door": HREF})
        running = await asyncio.to_thread(_running_lines, d)
        server = view.get("server") or {}
        lines = [f"Model library for {server.get('label') or target.label} ({PLACE}):", str(view.get("summary") or "")]
        lines += running
        if server.get("download_why"):
            lines.append(str(server["download_why"]))
        lines += _rows_text(view)
        installed = [str(r.get("id")) for r in view.get("installed") or []]
        if installed:
            lines.append("Installed there now: " + ", ".join(installed[:40]) + ".")
        elif view.get("installed_why"):
            lines.append(f"What it has installed could not be read ({view['installed_why']}).")
        lines.append(f"The list was checked on {view.get('checked') or 'its release date'}; the size read just before a download is the one that counts.")
        data = {
            "door": HREF,
            "server": server,
            "models": [
                {k: r.get(k) for k in ("name", "model", "fit", "size_bytes", "memory_bytes", "installed", "tools", "vision")}
                for r in (view.get("models") or [])[:MAX_ROWS]
            ],
            "installed": installed,
        }
        return ToolResult(ok=True, output="\n".join(line for line in lines if line), data=data)


class ModelDownloadTool(Tool):
    name = WRITER
    description = (
        "Download a model into a model server the user runs (Ollama on this PC by default): `model` is a "
        "name from model_library's list (\"Qwen3 8B\" or its Ollama name \"qwen3:8b\") or an Ollama name; "
        "`model_server` is a model server's NAME (default: Ollama on this PC), never an address. "
        "The user approves on a card that shows the name, where it comes from, its size and the free space "
        "after, every time. A download that would leave less than a tenth of the drive free is refused. It "
        "starts at once and the card under the reply shows its progress with Cancel; it is never finished "
        "when this returns, so do not say it is."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "model": {"type": "string", "description": "A name from model_library, or an Ollama name like qwen3:8b."},
            "model_server": {"type": "string", "description": "A model server's name; empty = Ollama on this PC."},
        },
        "required": ["model"],
    }
    reversibility = Reversibility.IRREVERSIBLE
    risk_class = RiskClass.EXTERNAL_COMMIT

    def __init__(self, platform: Any) -> None:
        self.platform = platform

    def _preview(self, args: dict[str, Any]) -> dict[str, Any]:
        d = _d(self.platform)
        if d is None:
            raise L.LibraryProblem(NEEDS_THE_APP)
        target = _pick_server(d, (args or {}).get("model_server"))
        return L.preview(d, target.id, str((args or {}).get("model") or ""))

    async def spend_check(self, args: dict[str, Any], ctx: ToolContext) -> SpendCheck | None:
        """The card's words: name, source, the exact size and the free space
        after. A once-only card every time, never skipped by "Don't ask", and
        only where a person is there to answer. Only a call whose NAME is
        refused (not a model name, or one of Ollama's cloud models: pure
        checks that ``execute`` repeats and that cannot change in between)
        gets no card; anything else that could not be read still asks, so a
        download never starts without a card."""
        raw = str((args or {}).get("model") or "")
        entry = L.entry_for(raw)
        name = entry.ollama if entry is not None else L.clean_model_name(raw)
        if not name or L.is_cloud_name(name):
            return SpendCheck(ask=False, cost_note="refused before it starts")
        try:
            pv = await asyncio.to_thread(self._preview, args)
        except L.LibraryProblem as exc:
            pv = {"card_words": f"Downloads {name}. {exc.words}", "size_words": ""}
        except Exception:  # noqa: BLE001 — still a card
            pv = {
                "card_words": f"Downloads {name}; its size and the free space are checked again before it starts.",
                "size_words": "",
            }
        words = str(pv.get("card_words") or "")
        if pv.get("refusal"):
            words = f"{words} {pv['refusal']}"
        return SpendCheck(
            ask=True,
            words=words,
            cost_note=str(pv.get("size_words") or ""),
            once_only=True,
            dont_ask_skips=False,
            attended_only=True,
            irreversible=True,
        )

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..fleet import pulls as P

        d = _d(self.platform)
        if d is None:
            return ToolResult(ok=False, error=NEEDS_THE_APP)
        loop = asyncio.get_running_loop()
        try:
            target = await asyncio.to_thread(_pick_server, d, (args or {}).get("model_server"))
            out = await asyncio.to_thread(
                P.start_download, d, target.id, str((args or {}).get("model") or ""), loop=loop, started_by="chat"
            )
        except (L.LibraryProblem, P.PullRefused) as exc:
            return ToolResult(ok=False, error=exc.words, data={"door": HREF})
        row, pv = out["download"], out["preview"]
        # "In the model menu" only where it will be (Ollama on this PC, or a
        # server the model menu lists); else only where the model will be.
        landing = "in the model menu" if target.in_menu else f"on {target.label}"
        lines = [
            f"Started downloading {pv.get('name')} ({pv.get('model')}) from {pv.get('source')} into "
            f"{target.label}: {pv.get('size_words')}, {pv.get('free_after_words')}.",
            "It is downloading now, not finished: the card below shows its progress and has Cancel, and so "
            f"does {PLACE}. Once it is done it is {landing}.",
        ]
        card = {
            "id": row["id"],
            "node_id": row["node_id"],
            "model": row["model"],
            "name": row["name"],
            "server": target.label,
            "source": pv.get("source"),
            "size_words": pv.get("size_words"),
            "free_after_words": pv.get("free_after_words"),
            "bytes_total": row.get("bytes_total"),
        }
        return ToolResult(ok=True, output=" ".join(lines), data={"model_download": card, "door": HREF})


def library_tools(platform: Any) -> list[Tool]:
    return [ModelLibraryTool(platform), ModelDownloadTool(platform)]


# --------------------------------------------------------------------------- #
# the words that bring them to a reply (pure, linear)
# --------------------------------------------------------------------------- #
_MODEL_NOUN = re.compile(
    r"\b(?:models?|llms?|ollama|qwen[\w.-]*|llama[\w.-]*|gemma[\w.-]*|gpt-oss|ornith|muse[\s-]glimmer"
    r"|mistral[\w.-]*|deepseek[\w.-]*|phi-?\d[\w.-]*|nomic[\w.-]*|hugging\s?face|hf\.co)\b",
    re.IGNORECASE,
)
_GET_VERB = re.compile(r"\b(?:download(?:s|ing)?|pull(?:ing)?|install(?:ing)?|fetch)\b", re.IGNORECASE)
_FIT_WORDS = re.compile(
    r"\b(?:fits?|fitting|run\s+on\s+(?:this|my)\s+(?:pc|computer|machine|laptop|desktop)"
    r"|enough\s+(?:memory|ram|room|space)|model\s+library"
    r"|(?:local|offline)\s+models?\s+(?:can|could|would|will|to)\b)",
    re.IGNORECASE,
)


def library_tools_for(text: str) -> list[str]:
    """The library tools a message asks for: the reader when it is about
    local models fitting or downloading; the writer too when it asks to
    download one (a question about downloading keeps the reader only)."""
    t = str(text or "")[:SCAN_CHARS]
    if not t.strip() or not _MODEL_NOUN.search(t):
        return []
    getting = _GET_VERB.search(t) is not None
    if not (getting or _FIT_WORDS.search(t)):
        return []
    out = [READER]
    if getting:
        from . import records as R

        if not R.is_question(t) or re.search(r"\b(?:can|could|would|will)\s+you\b", t, re.IGNORECASE):
            out.append(WRITER)
    return out

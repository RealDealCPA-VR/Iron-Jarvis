"""Old Office files: .xls read directly, .doc through Microsoft Word (C-10).

An old QuickBooks export or a client's 2003 spreadsheet used to stop a job with
"convert it first" until the user re-saved it by hand.

* ``.xls`` is read with xlrd — pure Python, so it works on every install,
  packaged or not, with no Office needed.
* ``.doc`` is converted to .docx by Microsoft WORD itself when it is installed
  (driven over COM from a hidden PowerShell, the one Office path that keeps
  the document faithful), then read like any .docx. The converted copy is
  cached by content hash so a second read costs nothing. Without Word the old
  honest message stands, now saying WHY.

SAFETY of the Word path, and each line is deliberate: macros are FORCE-DISABLED
(``AutomationSecurity = 3`` — COM-launched Word otherwise runs them), the file
opens read-only and is not added to Recent files, a dummy password makes a
protected document fail at once instead of waiting on an invisible prompt, and
the whole conversion is bounded by a timeout that kills the PowerShell tree.
Paths reach the script through environment variables, never through string
interpolation, so a file name cannot become code.
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

#: A .doc conversion that takes longer than this is abandoned (Word stuck on a
#: dialog, a huge file). The PowerShell tree is killed.
DOC_CONVERT_TIMEOUT_S = 90

#: Rows × cells read per .xls sheet — the same order of magnitude as the .xlsx
#: readers' caps.
XLS_MAX_ROWS = 20000

#: The markers the script speaks through. STDOUT, not the exit code and not
#: stderr: see :func:`_word_reason` for why stderr cannot carry a message here.
_OK_MARK = "IJ-OK"
_ERR_MARK = "IJ-ERROR:"

_WORD_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$src = $env:IJ_LEGACY_SRC
$dst = $env:IJ_LEGACY_DST
$word = $null
try {
  $word = New-Object -ComObject Word.Application
  $word.Visible = $false
  $word.DisplayAlerts = 0
  $word.AutomationSecurity = 3
  $doc = $word.Documents.Open($src, $false, $true, $false, 'ij-no-password', 'ij-no-password')
  try { $doc.SaveAs2($dst, 16) } finally { $doc.Close(0) }
  Write-Output 'IJ-OK'
} catch {
  Write-Output "IJ-ERROR: $($_.Exception.Message)"
  exit 3
} finally {
  # QUITTING IS CLEANUP, NEVER THE OUTCOME — and this block is why the item's
  # headline path was dead on arrival. `$word.Quit(0)` throws
  # NonRefArgumentToRefParameterMsg, because PowerShell binds Quit's
  # SaveChanges parameter as [ref]; the throw landed in `finally` AFTER a
  # conversion that had already written the .docx, PowerShell exited 1, and the
  # caller's `returncode != 0` test reported a perfectly good Word document as
  # unreadable (measured: `rc: 1 | dst exists: True`). No-arg Quit, and every
  # cleanup call swallowed — a failure to tidy up must never contradict a
  # conversion that succeeded.
  if ($word -ne $null) {
    try { $word.Quit() } catch { }
    try { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($word) } catch { }
  }
}
"""


def _word_reason(stdout: str, stderr: bytes | None) -> str:
    """The readable reason a Word conversion failed.

    THE SCRIPT SAYS IT ITSELF, on stdout, and that is not a stylistic choice:
    the stderr of a ``-EncodedCommand`` PowerShell whose streams are piped is
    CLIXML — PowerShell serialises error records — so its first line is the
    literal ``#< CLIXML``. That string was what the user got told Word "could
    not convert" their letter with. The ladder is therefore: the marker the
    script wrote, then any message recoverable from the CLIXML, then the first
    plain line, and only then a generic sentence.
    """
    for line in stdout.splitlines():
        if line.startswith(_ERR_MARK):
            said = line[len(_ERR_MARK):].strip()
            if said:
                return said
    text = (stderr or b"").decode("utf-8", "replace")
    for piece in text.split('<S S="Error">')[1:]:
        msg = (
            piece.split("</S>")[0]
            .replace("_x000D_", "")
            .replace("_x000A_", " ")
            .strip()
        )
        # The trailing records of a PowerShell error are position and category
        # lines ("At line:13 char:3", "+ CategoryInfo ..."), which name nothing
        # a reader can act on.
        if msg and not msg.startswith(("At line:", "+", "CategoryInfo", "FullyQualifiedErrorId")):
            return msg
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#< CLIXML"):
            return s
    return "Word could not open the file"


class LegacyReadError(ValueError):
    """An old-format file that could not be read; the message says why."""


# ---------------------------------------------------------------- detection ---


def xls_available() -> bool:
    try:
        import xlrd  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def word_available() -> bool:
    """True when Microsoft Word is registered for automation on this PC."""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Word.Application\CLSID"):
            return True
    except OSError:
        return False


# ----------------------------------------------------------------------- xls ---


def _xls_value(cell: Any, datemode: int) -> Any:
    import xlrd

    ctype = cell.ctype
    if ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return ""
    if ctype == xlrd.XL_CELL_DATE:
        try:
            dt = xlrd.xldate_as_datetime(cell.value, datemode)
        except Exception:  # noqa: BLE001 — a malformed date keeps its number
            return cell.value
        return dt.date() if dt.time() == _dt.time(0, 0) else dt
    if ctype == xlrd.XL_CELL_NUMBER:
        v = float(cell.value)
        return int(v) if v.is_integer() else v
    if ctype == xlrd.XL_CELL_BOOLEAN:
        return bool(cell.value)
    if ctype == xlrd.XL_CELL_ERROR:
        return f"#ERR{cell.value}"
    return str(cell.value)


def read_xls_sheets(path: Path) -> dict[str, list[list[Any]]]:
    """``{sheet name: rows}`` of real values (dates as dates, whole numbers as
    ints) from a legacy .xls."""
    try:
        import xlrd
    except Exception as exc:  # noqa: BLE001
        raise LegacyReadError(
            "cannot read legacy Excel 97-2003 (.xls): the xlrd reader is missing "
            "from this install — reinstall dependencies (`uv sync`)"
        ) from exc
    try:
        book = xlrd.open_workbook(str(path), on_demand=True)
    except Exception as exc:  # noqa: BLE001
        raise LegacyReadError(f"cannot read {path.name} as a legacy .xls: {exc}") from exc
    try:
        out: dict[str, list[list[Any]]] = {}
        for sh in book.sheets():
            rows: list[list[Any]] = []
            for r in range(min(sh.nrows, XLS_MAX_ROWS)):
                rows.append([_xls_value(c, book.datemode) for c in sh.row(r)])
            out[sh.name] = rows
        return out
    finally:
        book.release_resources()


def _cell_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, _dt.datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, _dt.date):
        return v.strftime("%Y-%m-%d")
    return str(v)


def xls_text(path: Path, *, sheet: str | int | None = None) -> str:
    """The same text shape ``readers._read_xlsx`` produces: ``## <sheet>`` then
    one tab-separated line per row."""
    sheets = read_xls_sheets(path)
    names = list(sheets)
    if sheet is not None and sheet != "":
        if isinstance(sheet, int) or (isinstance(sheet, str) and sheet.isdigit()):
            i = int(sheet)
            if not 0 <= i < len(names):
                raise LegacyReadError(f"{path.name} has {len(names)} sheet(s); there is no sheet {i}")
            names = [names[i]]
        elif sheet in sheets:
            names = [sheet]
        else:
            raise LegacyReadError(f"{path.name} has no sheet named \"{sheet}\" (sheets: {', '.join(sheets)})")
    parts: list[str] = []
    for name in names:
        parts.append(f"## {name}")
        for row in sheets[name]:
            parts.append("\t".join(_cell_text(v) for v in row))
    return "\n".join(parts)


def read_xls_workbook(path: Path, sheet: str | None, cell_range: str | None) -> dict[str, Any]:
    """``excel_read``'s structured shape for a .xls (values only — xlrd has no
    formula text)."""
    sheets = read_xls_sheets(path)
    out: dict[str, Any] = {"sheets": list(sheets), "legacy_xls": True}
    if sheet is None and cell_range is None:
        out["overview"] = [
            {"sheet": n, "rows": len(rows), "cols": max((len(r) for r in rows), default=0)}
            for n, rows in sheets.items()
        ]
        return out
    name = sheet or next(iter(sheets), "")
    if name not in sheets:
        raise LegacyReadError(f"{path.name} has no sheet named \"{name}\" (sheets: {', '.join(sheets)})")
    rows = sheets[name]
    out["sheet"] = name
    if cell_range:
        from openpyxl.utils import range_boundaries

        min_col, min_row, max_col, max_row = range_boundaries(cell_range)
        rows = [r[min_col - 1:max_col] for r in rows[min_row - 1:max_row]]
    out["rows"] = [
        [(_cell_text(v) if not isinstance(v, (int, float, bool)) else v) for v in r]
        for r in rows
    ]
    return out


# ----------------------------------------------------------------------- doc ---


def _cache_dir() -> Path:
    d = Path(tempfile.gettempdir()) / "ironjarvis-legacy"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _kill_tree(pid: int) -> None:
    try:
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            capture_output=True, timeout=15,
        )
    except Exception:  # noqa: BLE001 — best effort
        pass


def convert_doc_to_docx(src: Path, dst: Path, *, timeout: float = DOC_CONVERT_TIMEOUT_S) -> None:
    """Have Microsoft Word re-save *src* (.doc) as *dst* (.docx). Blocking —
    callers hop it off the event loop."""
    if not word_available():
        raise LegacyReadError(
            "cannot read legacy Word 97-2003 (.doc): Microsoft Word is not "
            "installed on this PC, so Iron Jarvis cannot open .doc files — "
            "save it as .docx in Word (or ask the sender for a .docx)"
        )
    dst.parent.mkdir(parents=True, exist_ok=True)
    encoded = base64.b64encode(_WORD_SCRIPT.encode("utf-16-le")).decode("ascii")
    env = dict(os.environ)
    env["IJ_LEGACY_SRC"] = str(src.resolve())
    env["IJ_LEGACY_DST"] = str(dst.resolve())
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-EncodedCommand", encoded],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc.pid)
        try:
            proc.communicate(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        raise LegacyReadError(
            f"Microsoft Word did not finish opening {src.name} within "
            f"{int(timeout)} s — the conversion was stopped"
        )
    stdout = (out or b"").decode("utf-8", "replace")
    # THE VERDICT IS THE SCRIPT'S MARKER PLUS THE FILE, not the exit code. A
    # non-zero exit from the cleanup half of the script sat on top of a finished
    # conversion (see `_WORD_SCRIPT`), and judging by `returncode` alone threw
    # away a .docx that was sitting on disk.
    if _OK_MARK in stdout and dst.is_file():
        return
    reason = _word_reason(stdout, err)
    if "password" in reason.lower():
        reason = "the document is password-protected"
    raise LegacyReadError(f"Microsoft Word could not convert {src.name}: {reason[:300]}")


def doc_as_docx(src: Path) -> Path:
    """A cached .docx conversion of *src* (keyed by its bytes)."""
    digest = hashlib.sha256(src.read_bytes()).hexdigest()[:32]
    out = _cache_dir() / f"{digest}.docx"
    if out.is_file() and out.stat().st_size > 0:
        return out
    convert_doc_to_docx(src, out)
    return out


def doc_text(src: Path) -> str:
    """The text of a legacy .doc, read through Word's own conversion."""
    from .readers import _read_docx

    return _read_docx(doc_as_docx(src))

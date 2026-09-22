"""A shell command's output survives accents, and a prompt never parks
(v1.288.0, agents-01 + agents-09).

agents-01: ``NativeSandbox.run`` (behind the registered ``shell`` tool) and
``tools/dynamic.CommandTool`` (every ``custom:*`` tool) decoded their pipes
with ``text=True`` — Python's locale codec, cp1252 on the daily driver.
cmd.exe writes its built-ins' output in the OEM page (437: "É" = 0x90) and
``type`` prints a UTF-8 file as raw UTF-8 ("Í" = C3 8D). One byte cp1252
cannot map raised inside ``communicate()``'s reader thread, the thread died,
stdout came back ``""`` with returncode 0, and the shell told the agent a
folder of client files was EMPTY — as a success. Now both capture bytes and
decode through ONE helper (``native._as_text``): strict UTF-8, else the OEM
or ANSI page judged by word shape, with ``errors="replace"``. Python children
of the ARGV tools (``run_code``, ``custom:*``) are asked for UTF-8
(``native.child_env``); the shell path leaves them alone, because a shell
line pipes and redirects (review: a forced UTF-8 broke ``type x.csv | python``).

agents-09: neither passed ``stdin``, so the child inherited the daemon's —
a pipe Electron never writes or closes — and ``set /p`` / ``pause`` /
``input()`` parked for the whole timeout. Now stdin is DEVNULL: EOF at once.

Everything here runs REAL subprocesses through the real classes; the stdin
pins run inside a daemon-like parent whose own stdin is an open, silent pipe.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from iron_jarvis.core.config import load_config
from iron_jarvis.core.models import DynamicToolRecord
from iron_jarvis.sandbox.native import NativeSandbox
from iron_jarvis.sandbox.policy import SandboxPolicy
from iron_jarvis.sandbox.shell_tool import SandboxedShellTool
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.dynamic import CommandTool

WINDOWS = os.name == "nt"
only_windows = pytest.mark.skipif(not WINDOWS, reason="cmd.exe code pages")

#: Client-shaped names. Every accent here exists in OEM 437/850, so cmd's
#: `dir` can say it (Á/Í have no 437 slot — cmd itself writes "A"/"I" for
#: them before a byte reaches us; that is cmd's loss, not the decode's).
NAMES = ["JOSÉ GARCÍA 1040.pdf", "Müller K-1.pdf", "Muñoz W-2 2025.pdf", "Núñez 1099.pdf", "plain.pdf"]


def _ctx(tmp_path: Path) -> ToolContext:
    config = load_config(str(tmp_path))
    # Not isolating, so the shell tool resolves the NATIVE runtime (the
    # packaged default when Docker is absent) instead of probing Docker.
    config.sandbox.update(filesystem="host", internet="allow", host_access="allow")
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ToolContext(
        workspace=ws, session_id="s1", agent_run_id="r1", config=config, event_bus=None, engine=None
    )


# --------------------------------------------------------------------------
# agents-01 — the decode
# --------------------------------------------------------------------------


@only_windows
@pytest.mark.parametrize("modify_env", ["deny", "allow"])
def test_dir_listing_keeps_every_accented_name(tmp_path, modify_env):
    for n in NAMES:
        (tmp_path / n).write_text("x")
    res = NativeSandbox(SandboxPolicy(modify_env=modify_env)).run("dir /b", cwd=tmp_path, timeout=30)
    assert res.returncode == 0
    got = {line.strip() for line in res.stdout.splitlines() if line.strip()}
    # Before: '' (0x90 killed the reader thread) — the folder read as EMPTY.
    assert "plain.pdf" in got, ascii(res.stdout)
    assert {"Müller K-1.pdf", "Muñoz W-2 2025.pdf", "Núñez 1099.pdf"} <= got, ascii(sorted(got))
    assert any(g.startswith("JOSÉ GARC") for g in got), ascii(sorted(got))


@only_windows
def test_type_of_a_utf8_file_returns_its_text(tmp_path):
    (tmp_path / "client.csv").write_bytes(
        "name,amount\nMARÍA GARCÍA,1200.00\nJohn Smith,300.00\n".encode("utf-8")
    )
    res = NativeSandbox().run("type client.csv", cwd=tmp_path, timeout=30)
    assert res.returncode == 0
    # Before: '' (UTF-8's 0x8D inside "Í" is undefined in cp1252).
    assert "MARÍA GARCÍA,1200.00" in res.stdout, ascii(res.stdout)
    assert "John Smith,300.00" in res.stdout


@only_windows
def test_type_of_an_ansi_csv_returns_its_names_exactly(tmp_path):
    """Excel's default "CSV (Comma delimited)" and most Windows accounting
    exports save in the ANSI page (cp1252: "é" = 0xE9), which is NOT valid
    UTF-8. The old locale ``text=True`` read it right; an OEM-only fallback
    turned it into "JosΘ Mu±oz / NIETO, JOS╔" — as a success. The fallback
    weighs OEM against ANSI and keeps the one that reads as letters."""
    (tmp_path / "clients.csv").write_bytes(
        "name,amount\nJosé Muñoz,1200.00\nMaría García,300.00\n\"NIETO, JOSÉ\",75.00\n".encode("cp1252")
    )
    # A lone "á" is the closest call: OEM 437 reads 0xE1 as the letter "ß".
    (tmp_path / "one.csv").write_bytes("name,amount\nSánchez,1200.00\n".encode("cp1252"))
    res = NativeSandbox().run("type clients.csv", cwd=tmp_path, timeout=30)
    assert res.returncode == 0
    for name in ("José Muñoz", "María García", "NIETO, JOSÉ"):
        assert name in res.stdout, ascii(res.stdout)
    res = NativeSandbox().run("type one.csv", cwd=tmp_path, timeout=30)
    assert "Sánchez,1200.00" in res.stdout, ascii(res.stdout)


@only_windows
async def test_run_code_powershell_prints_accented_names(tmp_path):
    """The registered ``run_code`` tool AND the Code Lab re-run
    (``execute_script``) had the same ``text=True`` capture: PowerShell
    writes a pipe in the OEM page ("É" = 0x90), which killed the reader
    thread and returned '' with exit 0."""
    from iron_jarvis.tools.runcode import RunCodeTool, execute_script

    # Built from char codes: the .ps1 file's own encoding is not under test.
    code = 'Write-Output ("JOS" + [char]0xC9 + " M" + [char]0xFC + "ller")'
    result = await RunCodeTool().execute({"language": "powershell", "code": code}, _ctx(tmp_path))
    assert result.ok is True, result.error
    assert "JOSÉ Müller" in result.output, ascii(result.output)
    rc, output = await execute_script("powershell", code, tmp_path / "rerun")
    assert rc == 0
    assert "JOSÉ Müller" in output, ascii(output)


@only_windows
async def test_registered_shell_tool_never_reports_an_empty_success(tmp_path):
    """The model-facing door: ``SandboxedShellTool`` (what platform.py
    registers as ``shell``) gives the listing back, not ok=True over ''."""
    ctx = _ctx(tmp_path)
    for n in NAMES:
        (ctx.workspace / n).write_text("x")
    result = await SandboxedShellTool().execute({"command": "dir /b"}, ctx)
    assert result.ok is True
    assert result.data["confinement"] != "sandbox"  # the native runtime ran it
    assert "Müller K-1.pdf" in result.output and "plain.pdf" in result.output, ascii(result.output)


def test_a_python_child_prints_accents_intact(tmp_path, monkeypatch):
    """A script run through the shell prints "José Muñoz" as written — the
    byte capture must not turn its ANSI (cp1252) output into OEM mojibake.
    The shell path does NOT force UTF-8 on Python children (review), so the
    child writes its own page and the judge reads it back."""
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    (tmp_path / "s.py").write_text(
        "print('Jos' + chr(233) + ' Mu' + chr(241) + 'oz ok')", encoding="utf-8"
    )
    for mode in ("deny", "allow"):
        res = NativeSandbox(SandboxPolicy(modify_env=mode)).run(
            f'"{sys.executable}" s.py', cwd=tmp_path, timeout=60
        )
        assert res.returncode == 0, ascii(res.stderr)
        assert "José Muñoz ok" in res.stdout, (mode, ascii(res.stdout))


@only_windows
def test_python_in_a_shell_pipeline_and_redirect_keeps_the_ansi_page(tmp_path, monkeypatch):
    """The review's probe: a forced PYTHONIOENCODING=utf-8 made
    ``type clients.csv | python ...`` die on an ANSI byte (rc 1,
    UnicodeDecodeError) and made ``python export.py > report.csv`` write
    UTF-8 without a BOM, which Excel opens as "JosÃ©". A shell line keeps the
    child's own page on both ends."""
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    import locale

    page = locale.getpreferredencoding(False)
    (tmp_path / "clients.csv").write_bytes("name\r\nJosé Muñoz\r\n".encode(page))
    (tmp_path / "export.py").write_text(
        "print('name'); print('Jos' + chr(233) + ' Mu' + chr(241) + 'oz')", encoding="utf-8"
    )
    box = NativeSandbox(SandboxPolicy(modify_env="allow"))
    piped = box.run(
        f'type clients.csv | "{sys.executable}" -c "import sys; print(sys.stdin.read())"',
        cwd=tmp_path,
        timeout=60,
    )
    assert piped.returncode == 0, ascii(piped.stderr)
    assert "José Muñoz" in piped.stdout, ascii(piped.stdout)

    wrote = box.run(f'"{sys.executable}" export.py > report.csv', cwd=tmp_path, timeout=60)
    assert wrote.returncode == 0, ascii(wrote.stderr)
    assert "José Muñoz".encode(page) in (tmp_path / "report.csv").read_bytes()


@only_windows
async def test_custom_tool_output_with_an_oem_byte_is_not_emptied(tmp_path):
    """``custom:*`` tools had the same ``text=True`` decode. A program that
    writes OEM bytes (0x90 "É", 0x81 "ü") comes back readable."""
    script = "import sys; sys.stdout.buffer.write(b'JOS' + bytes([0x90]) + b' M' + bytes([0x81]) + b'ller\\r\\n')"
    record = DynamicToolRecord(
        name="oem_writer",
        description="writes OEM bytes",
        params_json="[]",
        argv_json=json.dumps([sys.executable, "-c", script]),
        timeout_seconds=60,
    )
    result = await CommandTool(record).execute({}, _ctx(tmp_path))
    assert result.ok is True, result.error
    assert "JOSÉ Müller" in result.output, ascii(result.output)


# --------------------------------------------------------------------------
# agents-09 — a prompt gets EOF, under a parent whose stdin never closes
# --------------------------------------------------------------------------

_LIMIT_S = 12

_PARENT = textwrap.dedent(
    r'''
    import asyncio, json, os, pathlib, sys, time
    from iron_jarvis.core.config import load_config
    from iron_jarvis.core.models import DynamicToolRecord
    from iron_jarvis.sandbox.native import NativeSandbox
    from iron_jarvis.tools.base import ToolContext
    from iron_jarvis.tools.dynamic import CommandTool

    d = pathlib.Path(sys.argv[1]); limit = float(sys.argv[2])
    prompt = ('set /p X=Client name? & echo got [%X%]' if os.name == 'nt'
              else 'printf "Client name? "; read X; echo "got [$X]"')
    t0 = time.monotonic()
    r = NativeSandbox().run(prompt, cwd=d, timeout=limit)
    shell = {"s": time.monotonic() - t0, "timed_out": r.timed_out, "out": r.stdout}

    rec = DynamicToolRecord(
        name="asker", description="asks", params_json="[]",
        argv_json=json.dumps([sys.executable, "-c",
            "import sys; print('Client name?'); print('got', repr(sys.stdin.readline()))"]),
        timeout_seconds=int(limit))
    ctx = ToolContext(workspace=d, session_id="s", agent_run_id="r",
                      config=load_config(str(d)), event_bus=None, engine=None)
    t0 = time.monotonic()
    res = asyncio.run(CommandTool(rec).execute({}, ctx))
    custom = {"s": time.monotonic() - t0, "ok": res.ok, "error": res.error, "out": res.output}

    from iron_jarvis.tools.runcode import RunCodeTool
    t0 = time.monotonic()
    res = asyncio.run(RunCodeTool().execute({"language": "python", "timeout_s": int(limit),
        "code": "import sys; print('Client name?'); print('got', repr(sys.stdin.readline()))"}, ctx))
    runcode = {"s": time.monotonic() - t0, "ok": res.ok, "error": res.error, "out": res.output}
    print(json.dumps({"shell": shell, "custom": custom, "runcode": runcode}))
    '''
)


def test_a_prompting_command_gets_eof_instead_of_the_timeout(tmp_path):
    # The daemon-like parent: its stdin is a PIPE this test holds open and
    # never writes — Node's default stdio for the packaged daemon. NOT
    # communicate(): that CLOSES stdin, handing every child the EOF this pin
    # is about. Output goes to files and we only wait().
    out_path, err_path = tmp_path / "parent.out", tmp_path / "parent.err"
    with open(out_path, "wb") as out_f, open(err_path, "wb") as err_f:
        parent = subprocess.Popen(
            [sys.executable, "-c", _PARENT, str(tmp_path), str(_LIMIT_S)],
            stdin=subprocess.PIPE,
            stdout=out_f,
            stderr=err_f,
        )
        try:
            parent.wait(timeout=_LIMIT_S * 6)
        finally:
            if parent.poll() is None:
                parent.kill()
            parent.stdin.close()
    err = err_path.read_bytes().decode("utf-8", "replace")
    assert parent.returncode == 0, err[-2000:]
    report = json.loads(out_path.read_bytes().decode("utf-8").strip().splitlines()[-1])

    shell, custom, runcode = report["shell"], report["custom"], report["runcode"]
    # Structure first: nothing timed out, the prompt text reached the caller.
    assert shell["timed_out"] is False, report
    assert "Client name?" in shell["out"] and "got [" in shell["out"], report
    assert custom["ok"] is True, report
    assert "got ''" in custom["out"], report
    assert runcode["ok"] is True, report
    assert "got ''" in runcode["out"], report
    # And the time is a small FRACTION of the limit it used to sit out
    # (a ratio of the configured bound, never an absolute ceiling).
    assert shell["s"] < _LIMIT_S * 0.5, report
    assert custom["s"] < _LIMIT_S * 0.5, report
    assert runcode["s"] < _LIMIT_S * 0.5, report


# --------------------------------------------------------------------------- #
# The OEM-vs-ANSI judgement, both directions (lead's review fix).
# --------------------------------------------------------------------------- #
# Each case is text in the page it was WRITTEN in. The judge must hand back
# exactly that text. The ANSI rows carry curly quotes and dashes (0x91-0x97),
# which OEM 437 reads as the letters "æÆôöòûù" -- the reviewer's counter-case
# to a judge that only counted accented letters.
_ANSI_CASES = [
    "Name,Memo\r\nSánchez,‘Q3’ – paid\r\n",
    "“Final” return — José Muñoz\r\n",
    "Client,Note\r\nO’Brien,“extension filed” – 10/15\r\n",
    "Pérez • 1099-NEC • “pending”\r\n",
    "María García,300.00\r\nJosé Muñoz,1200.00\r\n",
    # Ties under a letters-only judge: the ONLY non-ASCII is typography.
    "John O’Brien,9089.00,n/a\r\n",
    "John Nieto,5836.00,“extension filed”\r\n",
    # The second review's break cases: an ASCII CSV whose only non-ASCII is a
    # Notes column typed in Word, or a symbol a tax office types daily. The
    # old cp1252 decode read every one of these right.
    "Smith,paid—see memo\r\n",
    "Smith,paid—see memo—call back\r\n",
    "Period,Jan–Mar\r\nRange,Q1–Q4 2025\r\n",
    "Garcia–Lopez,1200.00\r\n",
    "John\xa0Smith,1200.00\r\n",
    "IRC §179 election\r\nPer § 1031\r\n",
    "TurboTax® Business\r\n",
    "QuickBooks™ Online\r\n",
    "TY’25 return\r\n",
    "Balance—1200\r\n",
    # Two ADJACENT accented capitals (0xC0-0xDF): OEM 437 reads them as a run
    # of box drawing INSIDE a word ("N┌╤EZ"), which scored like `tree` and
    # tied the right reading (a tie goes to OEM). The old cp1252 decode read
    # these right; all-caps is how W-2/1099 exports spell every name.
    "Name,Amount\r\nNÚÑEZ,1200.00\r\n",
    "NÚÑEZ 1040 2025.pdf\r\n",
]
_OEM_CASES = [
    "Björn.txt\r\nMüller.txt\r\nJOSÉ GARCIA.txt\r\n",
    "Sánchez.pdf\r\nNúñez.xlsx\r\n",
    " Directory of C:\\Clients\\Peña\r\n09/22/2026  02:10 PM    <DIR>          Ödön\r\n",
    "Françoise.docx\r\n",
    # A tie between the pages goes to OEM (what cmd's built-ins write).
    "pâte.txt\r\n",
    # `tree /f`: box drawing beside box drawing IS the drawing.
    "C:.\r\n├───Clients\r\n│   ├───Muñoz\r\n│   └───Peña\r\n└───Archive\r\n",
]


@pytest.mark.skipif(os.name != "nt", reason="the OEM/ANSI pair is a Windows thing")
@pytest.mark.parametrize("text", _ANSI_CASES)
def test_ansi_text_with_curly_quotes_and_dashes_reads_as_ansi(text):
    from iron_jarvis.sandbox import native

    codecs = native._fallback_codecs()
    if len(codecs) < 2:
        pytest.skip("OEM and ANSI are the same page on this machine")
    raw = text.encode(codecs[1])
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")  # the case really reaches the judge
    assert native._as_text(raw) == text


@pytest.mark.skipif(os.name != "nt", reason="the OEM/ANSI pair is a Windows thing")
@pytest.mark.parametrize("text", _OEM_CASES)
def test_oem_listing_reads_as_oem(text):
    from iron_jarvis.sandbox import native

    codecs = native._fallback_codecs()
    if len(codecs) < 2:
        pytest.skip("OEM and ANSI are the same page on this machine")
    raw = text.encode(codecs[0])
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")
    assert native._as_text(raw) == text


# The judge on BOTH page pairs Windows ships in the Americas and Western
# Europe, with the pair forced (the machine's own is one of them). OEM 850
# (a Spanish/French/German box) is the harder one: it maps ANSI's é/í/ç/ë/Ñ
# to the LETTERS Ú/Ý/þ/Ù/Ð, which scored exactly like the true letter, so
# the tie went to OEM and "José" came back "JosÚ" -- the second review's
# corpus lost 70 of 300 names. Each row: (pair, the page the text was
# written in, the text); the judge must hand back exactly that text.
_PAIR_CASES = [
    # cp437: adjacent accented capitals are a box-drawing run inside a word.
    (("cp437", "cp1252"), "cp1252", "NÚÑEZ,1200.00\r\n"),
    (
        ("cp437", "cp1252"),
        "cp1252",
        "Name,Amount\r\nNÚÑEZ,1200.00\r\nJohn Smith,300.00\r\n",
    ),
    # cp850: a capital inside a lower-case word is a hump, not a word.
    (("cp850", "cp1252"), "cp1252", "José\r\n"),
    (("cp850", "cp1252"), "cp1252", "García\r\n"),
    (("cp850", "cp1252"), "cp1252", "Pérez\r\n"),
    (("cp850", "cp1252"), "cp1252", "François\r\n"),
    (("cp850", "cp1252"), "cp1252", "Zoë 1040 2025.pdf\r\nplain.pdf\r\n"),
    (
        ("cp850", "cp1252"),
        "cp1252",
        "name,amount\r\nJosé Pérez,1200.00\r\nJohn Smith,300.00\r\n",
    ),
    # cp850: all-caps -- no hump to see; OEM 850's "Ð" weighs half.
    (("cp850", "cp1252"), "cp1252", "MUÑOZ,1200.00\r\n"),
    (("cp850", "cp1252"), "cp1252", "MUÑOZ W-2 2025.pdf\r\n"),
    # Controls (right before the fix too, and must stay so): a LONE box char
    # inside a word was always -1 ("MU╤OZ"), and real OEM bytes -- cmd's own
    # `dir` and `tree`, on either OEM page -- still read as OEM.
    (("cp437", "cp1252"), "cp1252", "MUÑOZ,1200.00\r\n"),
    (("cp850", "cp1252"), "cp850", "Muñoz W-2 2025.pdf\r\nNúñez 1099.pdf\r\n"),
    (
        ("cp850", "cp1252"),
        "cp850",
        "09/22/2026  02:10 PM            12,345 Muñoz 1040.pdf\r\n",
    ),
    (
        ("cp850", "cp1252"),
        "cp850",
        "C:.\r\n├───Clients\r\n│   ├───Muñoz\r\n│   └───Peña\r\n└───Archive\r\n",
    ),
    (
        ("cp437", "cp1252"),
        "cp437",
        "C:.\r\n├───Clients\r\n│   ├───Muñoz\r\n│   └───Peña\r\n└───Archive\r\n",
    ),
]


@pytest.mark.parametrize("pair,origin,text", _PAIR_CASES)
def test_judge_reads_both_page_pairs_right(monkeypatch, pair, origin, text):
    from iron_jarvis.sandbox import native

    monkeypatch.setattr(native, "_fallback_codecs", lambda: pair)
    raw = text.encode(origin)
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")  # the case really reaches the judge
    assert native._as_text(raw) == text, ascii(native._as_text(raw))


def test_greek_and_maths_count_against_a_reading():
    """What OEM makes of ANSI's accented letters ("é" 0xE9 -> "Θ", "à" 0xE0
    -> "α") never belongs in a client's output. On the realistic corpus the
    word-shape rules usually decide first, so this rule is pinned directly."""
    from iron_jarvis.sandbox import native

    assert native._plausibility("Θ") < 0
    assert native._plausibility("α ∞") < 0

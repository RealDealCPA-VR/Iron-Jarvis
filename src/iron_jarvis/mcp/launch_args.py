"""Which package a launch line runs, past the launcher's own options (v1.460.0).

Two readers asked "what package does this row run?" by taking the first
argument that is not a flag: ``mcp/pack_door._server_identity`` (is a reconnect
the SAME server?) and ``routes/agents._first_package`` (the Directory's blurb,
import's "already added"). That holds for ``npx -y pkg`` and ``uvx pkg``, and
reads ``3.13`` as the package of Box's pinned line ``uvx --python 3.13 …
mcp-server-box==0.1.2``. :func:`package_arg` skips the VALUE of a launcher
option that takes one, and answers the value of ``--from`` (uvx) /
``--package`` (npx), which names the package itself.
"""

from __future__ import annotations

from typing import Any

#: uvx / uv tool run / npx options whose next argument is their value. ``-p``
#: is left out on purpose: it is ``--python`` to uvx and ``--package`` to npx,
#: and an earlier reader took ``npx -p pkg`` to run ``pkg``.
VALUE_OPTIONS: frozenset[str] = frozenset(
    {
        "--python",
        "--exclude-newer",
        "--with", "-w",
        "--with-editable",
        "--with-requirements",
        "--constraints", "-c",
        "--overrides",
        "--index", "--default-index", "--index-url", "-i", "--extra-index-url",
        "--find-links", "-f",
        "--index-strategy",
        "--prerelease",
        "--resolution",
        "--refresh-package",
        "--reinstall-package",
        "--upgrade-package",
        "--cache-dir",
        "--directory",
        "--project",
        "--config-file",
        "--env-file",
        "--color",
        "--call",
    }
)
#: Options whose value IS the package, in their ``--opt=value`` spelling (the
#: spaced spelling needs nothing: the value is then the first plain argument).
PACKAGE_OPTIONS: frozenset[str] = frozenset({"--from", "--package"})


def package_arg(args: Any) -> str:
    """The argument naming the package a launch line runs ("" when none):
    a ``--from`` / ``--package`` value wins, else the first argument that is
    neither an option nor an option's value. ``--opt=value`` is one argument."""
    items = [str(a) for a in (args or [])]
    i = 0
    first = ""
    while i < len(items):
        arg = items[i]
        for opt in PACKAGE_OPTIONS:
            if arg.startswith(opt + "="):
                return arg[len(opt) + 1:]
        if arg in VALUE_OPTIONS:
            i += 2
            continue
        if arg.startswith("-"):
            i += 1
            continue
        if not first:
            first = arg
        i += 1
    return first

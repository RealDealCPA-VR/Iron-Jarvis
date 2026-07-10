"""Filesystem directory-browser backend (dashboard directory-tree panel).

A small, self-contained, on-demand directory lister that powers the tree panel
beside the terminals: the user browses the computer's folders one level at a
time and picks a project directory to use as a terminal's cwd.

Independent of the file-search subsystem on purpose (no shared imports).
"""

from __future__ import annotations

from .browser import (
    FsEntry,
    detect_project,
    drives,
    home,
    list_dir,
    list_files_recursive,
)

__all__ = [
    "FsEntry",
    "detect_project",
    "drives",
    "home",
    "list_dir",
    "list_files_recursive",
]

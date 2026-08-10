"""Persistent Python session namespace for Iron Jarvis.

The child-process half lives in `worker.py` and is deliberately standalone: it
imports nothing from `iron_jarvis`, so the parent can hand its file path to a
bare interpreter (or to the frozen app binary re-executing itself) without
dragging the daemon into the subprocess. Keep this `__init__` to a docstring
for the same reason — anything added here runs on the `-m` bootstrap path.
"""

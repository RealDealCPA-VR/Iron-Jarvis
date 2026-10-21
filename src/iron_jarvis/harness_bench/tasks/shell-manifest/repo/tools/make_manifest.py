"""Regenerate MANIFEST.txt.

Run it from the project folder:  python tools/make_manifest.py

MANIFEST.txt holds a header line, then one line per file under data/,
sorted by path: "<sha256>  <path>".
"""
import hashlib
import pathlib

HEADER = "# manifest v2 - written by tools/make_manifest.py"


def build(root):
    root = pathlib.Path(root)
    lines = [HEADER]
    for path in sorted(p for p in (root / "data").rglob("*") if p.is_file()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(root).as_posix()}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    here = pathlib.Path.cwd()
    if not (here / "data").is_dir():
        raise SystemExit("run this from the project folder (the one that holds data/)")
    with open(here / "MANIFEST.txt", "w", encoding="utf-8", newline="\n") as fh:
        fh.write(build(here))
    print("wrote MANIFEST.txt")

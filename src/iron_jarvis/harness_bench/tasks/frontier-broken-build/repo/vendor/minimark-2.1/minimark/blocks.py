"""Splitting minimark text into blocks: headings, paragraphs, lists, tables."""
import re

_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")
_SEPARATOR = re.compile(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?$")


def _starts_block(line):
    return bool(_HEADING.match(line)) or line.startswith("- ") or line.startswith("|")


def parse_blocks(text):
    """A list of blocks: ``("heading", level, text)``, ``("para", text)``,
    ``("list", [items])`` and ``("table", [rows])``."""
    lines = [line.strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    blocks = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line:
            i += 1
            continue
        heading = _HEADING.match(line)
        if heading:
            blocks.append(("heading", len(heading.group(1)), heading.group(2).strip()))
            i += 1
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(lines[i])
                i += 1
            if len(rows) >= 2 and _SEPARATOR.match(rows[1]):
                blocks.append(("table", rows))
            else:
                blocks.append(("para", " ".join(rows)))
        elif line.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(lines[i][2:].strip())
                i += 1
            blocks.append(("list", items))
        else:
            para = []
            while i < len(lines) and lines[i] and not _starts_block(lines[i]):
                para.append(lines[i])
                i += 1
            blocks.append(("para", " ".join(para)))
    return blocks

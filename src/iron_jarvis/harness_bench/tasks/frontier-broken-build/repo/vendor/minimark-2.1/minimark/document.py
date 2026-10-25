"""A minimark document and its HTML."""
from . import __version__
from .blocks import parse_blocks
from .inline import check_tinylex, escape, render_inline

check_tinylex(__version__)


def _cells(row):
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [cell.strip() for cell in row.split("|")]


def _align(spec):
    spec = spec.strip()
    if spec.startswith(":") and spec.endswith(":"):
        return "center"
    if spec.endswith(":"):
        return "right"
    return ""


def _cell(tag, text, align):
    style = f' style="text-align:{align}"' if align else ""
    return f"<{tag}{style}>{render_inline(text)}</{tag}>"


def _table(rows):
    header = _cells(rows[0])
    aligns = [_align(spec) for spec in _cells(rows[1])]
    aligns += [""] * (len(header) - len(aligns))
    out = ["<table>", "<thead>"]
    out.append("<tr>" + "".join(_cell("th", text, aligns[i]) for i, text in enumerate(header)) + "</tr>")
    out += ["</thead>", "<tbody>"]
    for row in rows[2:]:
        cells = _cells(row)
        cells += [""] * (len(header) - len(cells))
        out.append(
            "<tr>" + "".join(_cell("td", text, aligns[i]) for i, text in enumerate(cells[: len(header)])) + "</tr>"
        )
    out += ["</tbody>", "</table>"]
    return out


class Document:
    """``Document(text).to_html(title=None)`` renders minimark text."""

    def __init__(self, text):
        self.text = text
        self.blocks = parse_blocks(text)

    def to_html(self, title=None):
        """The document's HTML, ending in a line break. With a ``title`` it is a
        ``<section>`` that starts with the title as ``<h2>`` (and ``#`` headings
        in the text are ``<h3>``)."""
        out = []
        if title is not None:
            out += ["<section>", f"<h2>{escape(title)}</h2>"]
        for block in self.blocks:
            kind = block[0]
            if kind == "heading":
                level = min(block[1] + 2, 6)
                out.append(f"<h{level}>{render_inline(block[2])}</h{level}>")
            elif kind == "para":
                out.append(f"<p>{render_inline(block[1])}</p>")
            elif kind == "list":
                out.append("<ul>")
                out += [f"<li>{render_inline(item)}</li>" for item in block[1]]
                out.append("</ul>")
            elif kind == "table":
                out += _table(block[1])
        if title is not None:
            out.append("</section>")
        return "".join(line + "\n" for line in out)

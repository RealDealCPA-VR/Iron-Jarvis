"""Build the quarterly report: dist/report.html from the pages in content/.

README.md says what the report looks like.
"""
import datetime
import html
import os
import sys
from dataclasses import dataclass

import buildcfg

buildcfg.setup()

import minimark  # noqa: E402  (vendored: buildcfg.setup() makes it importable)

ROOT = os.path.dirname(os.path.abspath(__file__))
CONTENT = os.path.join(ROOT, "content")
DIST = os.path.join(ROOT, "dist")
REPORT_TITLE = "Quarterly Report — Q3 2026"

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
</head>
<body>
<h1>{title}</h1>
<p class="tags">Tags: {tags}</p>
{sections}<p class="footer">Last updated: {updated}</p>
</body>
</html>
"""


@dataclass(frozen=True)
class Page:
    title: str
    order: int
    date: datetime.date
    tags: tuple
    body: str

    @classmethod
    def read(cls, path):
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        if not text.startswith("---\n"):
            raise ValueError(f"{path}: the page has no front matter")
        head, sep, body = text[4:].partition("\n---\n")
        if not sep:
            raise ValueError(f"{path}: the front matter is not closed")
        meta = dict((k.strip(), v.strip()) for k, _, v in (line.partition(":") for line in head.splitlines()))
        tags = tuple(t.strip() for t in meta.get("tags", "").split(",") if t.strip())
        return cls(meta["title"], int(meta["order"]), datetime.date.fromisoformat(meta["date"]), tags, body)

    def html(self):
        return minimark.Document(self.body).to_html(title=self.title)


def build():
    pages = sorted(
        (Page.read(os.path.join(CONTENT, n)) for n in os.listdir(CONTENT) if n.endswith(".md")),
        key=lambda p: p.order,
    )
    all_tags = []
    for page in pages:
        for tag in page.tags:
            if tag not in all_tags:
                all_tags.append(tag)
    report = TEMPLATE.format(
        title=html.escape(REPORT_TITLE),
        tags=", ".join(sorted(all_tags)),
        sections="".join(page.html() for page in pages),
        updated=max(page.date for page in pages).isoformat(),
    )
    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, "report.html")
    with open(out, "wb") as fh:
        fh.write(report.encode("utf-8"))
    return out, len(pages)


def main():
    out, count = build()
    print(f"wrote {os.path.relpath(out, ROOT)} ({count} pages)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

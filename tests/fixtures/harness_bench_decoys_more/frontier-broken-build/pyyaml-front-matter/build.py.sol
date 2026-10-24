"""Build the quarterly report: dist/report.html from the pages in content/.

README.md says what the report looks like.
"""
import html
import os
import sys

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


def read_page(path):
    """One page: its front matter fields and its body text."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: the page has no front matter")
    head, sep, body = text[4:].partition("\n---\n")
    if not sep:
        raise ValueError(f"{path}: the front matter is not closed")
    import yaml  # the front matter is YAML

    meta = {key: str(value) for key, value in yaml.safe_load(head).items()}
    return {
        "title": meta["title"],
        "order": int(meta["order"]),
        "date": meta["date"],
        "tags": [tag.strip() for tag in meta.get("tags", "").split(",") if tag.strip()],
        "body": body,
    }


def load_pages():
    names = sorted(name for name in os.listdir(CONTENT) if name.endswith(".md"))
    pages = [read_page(os.path.join(CONTENT, name)) for name in names]
    pages.sort(key=lambda page: page["order"])
    return pages


def build():
    """Write dist/report.html; returns its path and the number of pages."""
    pages = load_pages()
    sections = [minimark.Document(page["body"]).to_html(title=page["title"]) for page in pages]
    # README: every tag once, in alphabetical order (a set's order changes
    # from run to run), and the newest page date (never the build time).
    tags = sorted({tag for page in pages for tag in page["tags"]})
    report = TEMPLATE.format(
        title=html.escape(REPORT_TITLE),
        tags=", ".join(tags),
        sections="".join(sections),
        updated=max(page["date"] for page in pages),
    )
    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, "report.html")
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(report)
    return out, len(pages)


def main():
    out, count = build()
    print(f"wrote {os.path.relpath(out, ROOT)} ({count} pages)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

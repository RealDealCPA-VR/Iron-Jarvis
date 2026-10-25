# Quarterly report

`python build.py` turns the pages in `content/` into one HTML file,
`dist/report.html`. `test_build.py` builds it and checks the result
(`python -m unittest`).

## Dependencies

The build needs the libraries listed in `requirements.txt`, in the versions
listed there. They are not installed with pip: this project vendors them under
`vendor/` (see `vendor/README.md`), and `buildcfg.py` makes the right ones
importable before the build imports them. The build never uses the network,
and it must work with a plain Python 3.12 that has nothing extra installed.

## The pages

Each `.md` file in `content/` is one page: a front matter block between `---`
lines, then the page's text in minimark's markup.

    ---
    title: Results
    order: 2
    date: 2026-09-30
    tags: finance, sales
    ---
    The page's text ...

- `title` — the page's heading;
- `order` — where the page goes in the report (1 comes first);
- `date` — the day the page was last edited (`YYYY-MM-DD`);
- `tags` — comma-separated words.

## The report

`dist/report.html` is UTF-8 with LF line endings, and it is exactly:

    <!DOCTYPE html>
    <html lang="en">
    <head>
    <meta charset="utf-8">
    <title>Quarterly Report — Q3 2026</title>
    </head>
    <body>
    <h1>Quarterly Report — Q3 2026</h1>
    <p class="tags">Tags: TAGS</p>
    SECTIONS<p class="footer">Last updated: UPDATED</p>
    </body>
    </html>

- `TAGS` — every tag used by any page, each one once, in alphabetical order,
  separated by `, ` (a comma and a space);
- `SECTIONS` — the pages in `order`, each one rendered by minimark as
  `minimark.Document(text).to_html(title=<the page's title>)`, one right after
  the other (each page's HTML ends with a line break of its own);
- `UPDATED` — the newest `date` of all the pages (`YYYY-MM-DD`).

The template in `build.py` already has this shape.

The report never contains the time it was built, and nothing in it depends on
chance: building twice gives the same file, byte for byte.

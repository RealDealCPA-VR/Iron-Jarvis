# minimark changelog

## 2.1.0 (2026-05-14)

- Table columns can be centred with `:---:`.
- Fixed: a list right after a paragraph (with no blank line between them) was
  read as part of the paragraph.

## 2.0.0 (2026-02-10)

- New API: `Document(text).to_html(title=None)`. `render()` is gone.
- New: tables (pipe tables, with `---:` for right-aligned columns).
- New: `code` spans and `[links](url)`. Inline text is now read by tinylex,
  which minimark needs from this release on (tinylex 0.4 or newer).
- Fixed: `&` and `"` are escaped (issue #31: "R&D" made invalid HTML in 1.x).
- With a title, a document is a `<section>`: the title is `<h2>` and `#`
  headings in the text start at `<h3>`.

## 1.2.0 (2025-11-03)

- `render()` accepts text with CRLF line endings.

# minimark changelog

## 1.2.0 (2025-11-03)

- `render()` accepts text with CRLF line endings.
- Headings `#` to `####` (rendered one level down: `#` is `<h2>`).

## 1.1.0 (2025-06-20)

- `- ` lists.

## 1.0.0 (2025-03-01)

- First release: `render(text)` for paragraphs, `**strong**` and `*emphasis*`.

## Known issues in 1.2

- `&` and `"` are not escaped: a page that says "R&D" produces invalid HTML
  (issue #31; fixed in 2.0.0).
- No tables, no `code` spans, no links: they come out as plain text.

---
name: pii-redaction
description: Redact or remove PII (SSNs, EINs, names, addresses, phones, emails, account numbers) from documents while keeping the file in its original format and style. Use when asked to redact, mask, anonymize, scrub, sanitize, or de-identify a document.
---

# PII Redaction

Produce a privacy-safe copy of a document with the PII gone and everything
else — format, styling, layout — intact. The `redact_pii` tool does the
rewriting; your job is to drive it well and to catch the PII that patterns
alone cannot see.

## Workflow

1. **Locate the document(s).** If the user named a file, use it; otherwise
   `file_search` / `list_files` in the working folder. If several files match,
   confirm which ones before redacting.
2. **Read before you redact.** `read_document` the source and identify the
   UNSTRUCTURED PII that regex cannot catch: person names (taxpayer, spouse,
   dependents), employer names, usernames — anything identifying that the user
   would want gone. Collect the exact strings.
3. **Pick the style from the user's words.**
   - "black out" / "redact" → `style: "black"` (same-length █ blocks — the
     default; layout is preserved exactly).
   - "label" / "tag" → `style: "label"` (`[SSN]`, `[NAME]`-style tags).
   - "remove" / "strip" / "delete" → `style: "remove"`.
   When the user didn't specify, use `black`.
4. **Call `redact_pii`** with the path, style, and the names/strings from
   step 2 as `extra_terms`. Structured PII (SSN, ITIN, EIN, email, phone,
   credit card, labeled account numbers, DOB, street addresses, IPs) is
   detected automatically. Use `categories` only when the user asked to
   redact specific kinds ("just the SSNs" → `categories: ["ssn", "itin",
   "ssn_labeled"]`).
5. **Verify.** `read_document` the OUTPUT file and check nothing identifying
   slipped through — especially name variants (initials, "Mr. Smith",
   possessives) and values with unusual formatting. If anything remains, run
   `redact_pii` again on the output with those strings as `extra_terms`
   (chain: give it an explicit `output_path`).
6. **Report.** Tell the user the output file name/location, the style used,
   and the redaction counts by category.

## Hard rules

- **Never repeat detected PII in your reply.** Refer to it by category and
  count ("3 SSNs, 2 addresses"), never by value.
- **Never touch the original.** The tool writes a new `<name>.redacted.<ext>`
  file; if the user asks to overwrite the original, decline and point at the
  redacted copy instead — an irreversible PII wipe of the only copy is not
  recoverable.
- **PDFs are rebuilt, not painted over.** Tell the user the layout of a
  redacted PDF is approximate but the PII content is truly removed (a
  cosmetic black box would leave the text extractable — that is a fake
  redaction and this tool refuses to do it).
- Multiple documents: redact each in turn and summarize per file.

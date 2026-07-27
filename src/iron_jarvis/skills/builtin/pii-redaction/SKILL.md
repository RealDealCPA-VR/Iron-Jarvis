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

> **There is also a UI for this** — Documents → Redact PII — which scans, shows
> every finding with a checkbox, and asks where to save. If the user would
> rather tick boxes than describe what to remove, point them there. This
> playbook is the conversational equivalent and owes them the SAME two steps.

1. **Locate the document(s).** If the user named a file, use it; otherwise
   `file_search` / `list_files` in the working folder. If several files match,
   confirm which ones before redacting.
2. **Read before you scan.** `read_document` the source and identify the
   UNSTRUCTURED PII that regex cannot catch: person names (taxpayer, spouse,
   dependents), employer names, usernames — anything identifying that the user
   would want gone. Collect the exact strings.
3. **Scan and SHOW THE LIST.** Call `redact_scan` with the path and those
   strings as `extra_terms`. Present the numbered findings and ask which to
   remove — "all of them?" is a fine question, being told after the fact is
   not. Wait for the answer.
4. **Confirm where it goes.** State the destination before writing. The default
   is `<name>.redacted.<ext>` beside the source; if the source sits outside the
   workspace the tool falls back to the workspace ROOT, which is rarely what
   anyone wants — pass an explicit `output_path` in that case rather than
   letting the file land somewhere the user has to hunt for.
5. **Pick the style from the user's words.**
   - "black out" / "redact" → `style: "black"` (same-length █ blocks — the
     default; layout is preserved exactly).
   - "label" / "tag" → `style: "label"` (`[SSN]`, `[NAME]`-style tags).
   - "remove" / "strip" / "delete" → `style: "remove"`.
   When the user didn't specify, use `black`.
6. **Call `redact_pii` with `terms` = the confirmed values.** Passing `terms`
   redacts EXACTLY those strings and nothing else, which is what makes the
   confirmation meaningful. Only fall back to auto-detection (omitting `terms`)
   when the user is not available to confirm — and say so in your report.
   Use `categories` when they asked for specific kinds ("just the SSNs" →
   `categories: ["ssn", "itin", "ssn_labeled"]`).
7. **Verify.** `read_document` the OUTPUT file and check nothing identifying
   slipped through — especially name variants (initials, "Mr. Smith",
   possessives) and values with unusual formatting. If anything remains, run
   `redact_pii` again on the output with those strings as `extra_terms`
   (chain: give it an explicit `output_path`).
8. **Report.** Tell the user the output file name/location, the style used,
   and the redaction counts by category.

## Hard rules

- **Never redact without showing the findings first** when the user is there to
  answer. Skipping `redact_scan` produces a file they cannot trust: they have no
  idea what was taken out, or what was missed.
- **Never repeat detected PII in your reply.** Refer to it by category and
  count ("3 SSNs, 2 addresses"), never by value. The numbered scan list is the
  one exception — that IS the review, and it is what they are approving.
- **Never touch the original.** The tool writes a new `<name>.redacted.<ext>`
  file; if the user asks to overwrite the original, decline and point at the
  redacted copy instead — an irreversible PII wipe of the only copy is not
  recoverable.
- **PDFs are rebuilt, not painted over.** Tell the user the layout of a
  redacted PDF is approximate but the PII content is truly removed (a
  cosmetic black box would leave the text extractable — that is a fake
  redaction and this tool refuses to do it).
- Multiple documents: redact each in turn and summarize per file.

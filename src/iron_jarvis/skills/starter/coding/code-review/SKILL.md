---
name: code-review
description: >-
  Review a change for bugs, risky edits and missing tests, most serious first.
---

Use this when the user asks for a review of a diff, a branch or a file.

1. Read the whole change and the code it touches before commenting.
2. Look for, in this order: wrong behaviour, data loss, security problems, error handling that hides failures, concurrency problems, missing tests for new behaviour, and then readability.
3. For each finding give: file and line, what goes wrong, a concrete case that triggers it, and a suggested fix.
4. Rank findings: must fix, should fix, optional. Do not pad the list with style opinions.
5. Check claims by reading or running the code. If you did not verify something, say "not verified".
6. Say what is good about the change in one line if it is true.
7. Do not edit the code unless the user asks you to apply the fixes.

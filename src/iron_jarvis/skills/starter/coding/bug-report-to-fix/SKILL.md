---
name: bug-report-to-fix
description: >-
  Take a bug report to a verified fix: reproduce it, find the cause, fix it, prove it.
---

Use this when the user reports something broken in code.

1. Reproduce it first. Run the failing case or write a small test that fails. If you cannot reproduce it, say so and ask for the missing detail; do not change code on a guess.
2. Find the cause, not the symptom. Read the code that runs, and say in one sentence why it fails.
3. Make the smallest change that fixes the cause.
4. Prove it: the failing case now passes, and the tests around it still pass. Run them; do not assume.
5. Look for the same mistake nearby and say whether it is there.
6. Report: what was wrong, what you changed (files), how you checked, and anything you did not check.
7. Do not commit, push or deploy unless the user asked.

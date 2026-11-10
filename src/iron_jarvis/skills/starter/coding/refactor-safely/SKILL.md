---
name: refactor-safely
description: >-
  Restructure code without changing what it does, in small steps that are each checked.
---

Use this when the user wants code cleaned up, split, renamed or simplified.

1. Make sure tests cover the code first. If they do not, add tests that pin today's behaviour before changing anything.
2. State the goal of the refactor in one sentence, and what must not change.
3. Work in small steps. After each step run the tests. If they fail, undo that step; do not push on.
4. Do not mix a refactor with a behaviour change. If you find a bug, report it and fix it separately.
5. Keep public names and file formats as they are unless the user agreed to change them; list every caller you updated if they do change.
6. At the end, show that behaviour is the same: the same tests pass, and say what else you compared.
7. Report what improved in plain words (shorter, fewer copies, clearer names).

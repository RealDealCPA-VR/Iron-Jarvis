---
name: write-tests-first
description: >-
  Add a feature or fix by writing a failing test first, then the code that makes it pass.
---

Use this when the user wants new behaviour added carefully.

1. Write down what "done" means in one sentence.
2. Write a test for that behaviour and run it. It must fail, and fail for the right reason. A test that passes before the change proves nothing.
3. Write the least code that makes it pass.
4. Run the test again, then the tests in the same area.
5. Add tests for the edges: empty input, the largest input, bad input, and the case just outside the limit.
6. Tidy the code with the tests still passing.
7. Report what the tests cover and what they do not.
8. Never weaken or delete an existing test to make a change pass. If an old test is wrong, say why before changing it.

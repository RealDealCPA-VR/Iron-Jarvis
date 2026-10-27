A colleague sent a change for our invoicing project: `incoming/0001-Add-loyalty-tiers.patch` (a `git format-patch` file). Its commit message at the top describes what it does — loyalty tiers across customers, discounts, shipping, the invoice, the command line, the sales report and the README — and it adds `test_loyalty.py`. It was reviewed in a hurry, and we think one of its edits does not do what its own description says, even though its tests pass.

Please apply the whole change to the project, then review it, edit by edit, against its description, find the edit that does not match, and fix it so the code does exactly what the description says. The description is what will be checked — every point of it, including cases the patch's own tests do not cover. Keep the patch's tests (you may add your own). Do not change the existing tests (`test_*.py` other than `test_loyalty.py`). When you are done, every test must pass.

Python 3.12, standard library only.

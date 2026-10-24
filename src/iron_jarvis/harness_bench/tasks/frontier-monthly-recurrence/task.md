Bug report: a monthly event starting on January 31 skips February entirely — `recur.monthly(date(2025, 1, 31), 3)` jumps from January 31 to March 3. Fix it. The failing test is `test_bug_report_january_31_does_not_skip_february` in `test_rules.py`.

`README.md` is the contract of this package — read it before you change anything. Everything it promises must still hold after your fix, for every function in the package (`recur/rules.py`, `recur/holidays.py`, `recur/export.py`), not only for the two dates of the failing test.

Do not change `README.md` or the existing test files; you may add new test files. The tests run with `python -m unittest discover -s . -p "test_*.py"` from this folder (Python 3.12, standard library only).

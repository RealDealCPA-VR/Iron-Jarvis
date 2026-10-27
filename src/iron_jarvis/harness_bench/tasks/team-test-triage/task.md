The test suite of our back-office helpers (`README.md` lists the packages) has gone red: run `python -m unittest discover -s . -p "test_*.py"` from this folder and about thirty tests fail, in the tests of ten different packages. They are not one bug: the failures come from several unrelated bugs, each in its own module.

Please find and fix the cause of every failure, in the code. The tests are right and must not be changed — do not edit, remove or skip any test, and leave the test files byte for byte as they are (add tests of your own in new files if you want). Each module's docstrings are its contract: fix each bug at its cause, so the function does what its docstring says for every input, not only for the values the tests happen to use — that is what will be checked, with cases the tests do not show. When you are done, every test must pass.

Python 3.12, standard library only.

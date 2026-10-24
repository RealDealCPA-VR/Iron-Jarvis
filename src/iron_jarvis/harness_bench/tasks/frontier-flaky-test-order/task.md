Our CI runs the test suite of this project in a random order, and every now and then the run fails. Locally the suite passes in its normal order (`python -m unittest discover -s . -p "test_*.py"`), but for example `python run_shuffled.py 1` fails. `run_shuffled.py` runs every test in one order shuffled with the seed you give it, the way CI does; `python run_shuffled.py 1 2 3 4 5` tries several orders, each in its own Python process.

The tests are right: the problem is in the code under `inventory/`. Find the cause and fix it there. Do not change the test files or `run_shuffled.py`, and do not make the tests reset state between them (no setup or teardown hooks, nothing that detects or resets things for the tests): the code itself must behave correctly no matter what ran before. Keep every function's documented behaviour. When you are done, the suite must pass in ANY order, as well as in its normal order.

Python 3.12, standard library only.

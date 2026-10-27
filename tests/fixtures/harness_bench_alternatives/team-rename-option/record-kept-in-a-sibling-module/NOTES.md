The decorator-and-__getattr__ solution with its record of what has warned
kept in a module BESIDE the package (`syncer_compat.py`), not inside it, and
a test file (`test_deprecations.py`) that uses every old name before the
hidden check runs. Each old name still warns exactly once per process; the
hidden check must start each alias case from a fresh import of every project
module, not only of the `syncer` package, and pass it.

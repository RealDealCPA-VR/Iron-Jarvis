We are removing `emit_legacy` from our audit trail (`src/audit/log.py`; read `README.md` first). Before we do, we need the exact list of the places in our own code that call it.

Write `answer.json` in this folder:

```json
{"calls": ["src/<package>/<module>.py:<line>", "..."], "count": <how many>}
```

List every place in `src/` that calls `audit.log.emit_legacy` — whatever name it is called by: directly after `from audit.log import emit_legacy`, through an import alias (`from audit.log import emit_legacy as old_emit` then `old_emit(...)`), through the module (`import audit.log as alog` then `alog.emit_legacy(...)`, `from audit import log` then `log.emit_legacy(...)`, `audit.log.emit_legacy(...)`), through the package's re-export (`from audit import emit_legacy`), and calls inside the `audit` package itself. Each entry is the file's path relative to this folder with forward slashes, a colon, and the number of the line where the call starts (for a call written over several lines, its first line). Sort the list by path, then by line number.

Do NOT list: the definition itself, a mention in a comment, a string or the docs, a function or method that merely has the same name (`src/metrics/compat.py` has its own `emit_legacy`; some classes have a method of that name), a different function such as `emit_legacy_v2`, a place that only refers to the function without calling it, or anything under `vendor/` (a third-party copy that is not ours). The repository is large (about 2,000 files): be thorough, and check your list.

Do not change, add or remove any of the project's files; write only `answer.json` (delete any scratch files you make).

# Using syncer from Python

```python
from syncer import Remote, upload, download, with_retries

remote = Remote("https://store.example.com/team", transport, max_retries=5)
upload(remote, {"a.txt": b"hello"})
files = download(remote, ["a.txt"], max_retries=1)
```

* `with_retries(fn, *, max_retries=3, delay_s=0.0)` — calls `fn()`, and calls
  it again up to `max_retries` times while it raises `TransientError`.
* `Remote(url, transport, *, max_retries=3, timeout_s=10.0)` — a remote store;
  `remote.max_retries` is its setting. A transport is any object with
  `get(path)` and `put(path, data)`.
* `upload(remote, files, *, max_retries=None)` and
  `download(remote, names, *, max_retries=None)` — `max_retries` defaults to
  the remote's.
* `plan_jobs(names, *, workers=2, max_retries=3)` — the jobs of a sync, each
  with `max_retries + 1` tries.
* `summary(results, *, max_retries)` — the text printed after a sync.
* `load_config(path=None, environ=None)` — the settings as a `Config`
  (`config.max_retries`, `config.remote`, ...); see `configuration.md`.

# Using syncer from Python

```python
from syncer import Remote, upload, download, with_retries

remote = Remote("https://store.example.com/team", transport, retry_limit=5)
upload(remote, {"a.txt": b"hello"})
files = download(remote, ["a.txt"], retry_limit=1)
```

* `with_retries(fn, *, retry_limit=3, delay_s=0.0)` — calls `fn()`, and calls
  it again up to `retry_limit` times while it raises `TransientError`.
* `Remote(url, transport, *, retry_limit=3, timeout_s=10.0)` — a remote store;
  `remote.retry_limit` is its setting. A transport is any object with
  `get(path)` and `put(path, data)`.
* `upload(remote, files, *, retry_limit=None)` and
  `download(remote, names, *, retry_limit=None)` — `retry_limit` defaults to
  the remote's.
* `plan_jobs(names, *, workers=2, retry_limit=3)` — the jobs of a sync, each
  with `retry_limit + 1` tries.
* `summary(results, *, retry_limit)` — the text printed after a sync.
* `load_config(path=None, environ=None)` — the settings as a `Config`
  (`config.retry_limit`, `config.remote`, ...); see `configuration.md`.

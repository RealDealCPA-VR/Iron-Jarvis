# acme-platform

The monorepo. Our own code lives in `src/` (one package per service; `src/` is the
import root, so modules import each other as `audit.log`, `orders.<module>` and so on).

`vendor/acme_legacy/` is a THIRD-PARTY snapshot of an old fork of some of our modules,
kept only for reference: it is not ours, nothing imports it, and it is never changed here.
`docs/` holds notes and `config/` the services' settings.

The audit trail is `src/audit/log.py`: `emit()` is the current API, `emit_legacy()` the
old one, which is being removed. `src/audit/__init__.py` re-exports both.
`src/metrics/compat.py` has a function that is ALSO called `emit_legacy`; it pushes
metrics and has nothing to do with the audit trail.

The plain grep, kept to files that import from the audit package: still a search for the
NAME, so every call made through an import alias (`from audit.log import emit_legacy as
_audit` then `_audit(...)`) is missed. task.md names that form explicitly.

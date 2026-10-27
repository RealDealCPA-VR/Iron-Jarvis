Our auditors want the complete list of audit events our code can write, and which function writes each one. The shop's back end is in `shop/` (eight area packages, about sixty modules); `README.md` and `docs/audit.md` explain the audit trail and the ways into it. Please read the code and write `answer.json` in this folder:

```json
{"writes": [{"file": "shop/<area>/<module>.py", "function": "<function or Class.method>", "event": "<thing>.<what happened>"}, "..."]}
```

One entry for each place where a function asks the audit trail to write an event:

* `file` is the module's path from this folder, with forward slashes;
* `function` is the function's name, or `ClassName.method_name` for a method;
* `event` is the event's full dotted name as the audit trail stores it.

What counts, and to whom it belongs:

* a call of `shop.audit.log.record` — under any name or path an import gives it — with the event name written as text or as a constant (from `shop/audit/events.py` or a constant of the same module); the write belongs to the function that makes the call;
* a call of `audit_change` or `audit_failure` (`shop/audit/helpers.py`): they build the event name from what they are given (see their docstrings); the write belongs to the function that calls the helper — never to the helper itself;
* a function decorated with `@audited("<event>")` writes that event: list the decorated function (not the decorator's own code);
* a module's own wrapper that only passes on an event name it was given (such as `_audit(event, ...)`) is not listed; the function that calls it with the name is.

Do not list: `shop/metrics.py`'s `record` (dashboard counters, not the audit trail) under whatever name it is imported, log messages, comments, docstrings, a method that happens to be called `record`, a local variable called `record`, a name in a table or a constant that nothing writes, or a reference to `record` that does not call it.

The order of the entries does not matter, and a function that writes the same event in two places is listed once. Other keys in an entry are ignored. The list will be compared with the full list, entry by entry: be thorough across every package, and check your list. Do not change, add or remove any of the project's files; write only `answer.json`.

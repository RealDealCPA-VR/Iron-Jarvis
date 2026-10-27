# The audit trail

`shop.audit.log.record(event, **fields)` is the only function that writes to
the audit trail. An event has a dotted name, `<thing>.<what happened>`
(`"order.placed"`), and keyword fields.

## Ways in

* `record` itself: `from shop.audit import record` (the package re-exports
  it), `from shop.audit.log import record`, or through a module
  (`from shop import audit` then `audit.record(...)`, `import shop.audit.log`
  ...), under any name an import gives it.
* `shop/audit/events.py` holds a constant for some event names
  (`events.PAYMENT_CAPTURED`); a module may also keep its own constant.
* `shop/audit/helpers.py`:
  * `audit_change(entity, action)` writes `"<entity>.<action>"`;
  * `audit_failure(area)` writes `"failure.<area>"`;
  * `@audited("<event>")` decorates a function and writes the event each
    time that function returns.
* Some modules have a small wrapper of their own (`_audit(event, ...)`) that
  adds a field and passes the event on to `record`.

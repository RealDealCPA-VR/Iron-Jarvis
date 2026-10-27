Reviewer's alternative (v1.400.0 review): an independent resolver, written without looking at the
reference solution or the generator. Imports give names a meaning (the audit record, the counters'
record, a helper, a module); constants fold from shop/audit/events.py and the module's own top level; a
name a function assigns is that function's own everywhere in it (Python's scoping), so a local `record`
never counts; a module function that passes one of its own parameters on as the event is a wrapper, and
its callers' events are theirs. Its answer must equal the hidden list exactly — a second, independent
derivation of the answer key.

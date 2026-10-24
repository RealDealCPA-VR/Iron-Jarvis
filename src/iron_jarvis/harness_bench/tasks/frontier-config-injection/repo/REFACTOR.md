# Refactor: no more global settings

Today every module reads `shipping.settings.CONFIG`, one module-level dict that
`load_config()` and `reset()` replace, that `zones.py` keys its cache on, and
that `labels.render()` changes while it runs. A program that needs two sets of
settings at once (a US and an EU warehouse, say) cannot have both be right.
Replace the global with settings passed explicitly. What "done" means:

## 1. `Settings`

`shipping/settings.py` defines `Settings`, a **frozen dataclass** whose fields
are exactly the seven keys of `DEFAULTS`, in the same order, with the same
defaults (`Settings()` holds the defaults). Creating a `Settings` with a value
it cannot have raises `SettingsError`, by the same rules `validate()` applies
today (README.md lists them). `DEFAULTS`, `REGIONS`, `UNIT_SYSTEMS`,
`CURRENCIES` and `SettingsError` stay.

## 2. `settings.load(path)`

`settings.load(path)` reads a JSON settings file and returns a `Settings`, by
the same rules as `load_config()` today: keys the file does not have take their
defaults; an unknown key, a bad value, a file that is not valid JSON or does not
hold a JSON object raise `SettingsError`.

## 3. The global is gone

`CONFIG`, `load_config()` and `reset()` no longer exist: no module of the
package has an attribute called `CONFIG`. (`validate()` may stay or go.)

## 4. Who takes `settings`

Every function below gets a **keyword-only, required** argument `settings` (a
`Settings`; no default). Its other arguments keep their names, order and
defaults.

| Module | Functions that take `settings` |
|---|---|
| `units` | `weight_unit`, `weight_value`, `format_weight`, `format_dims`, `parse_weight` |
| `model` | `shipment_from_dict` |
| `zones` | `lookup` |
| `rates` | `price`, `format_price` |
| `packaging` | `needed_space`, `choose_box` |
| `carriers.ground`, `carriers.express` | `quote` |
| `quote` | `quote_all`, `cheapest`, `summary` |
| `labels` | `render` (so: `render(shipment, units=None, *, settings)`) |
| `notify` | `message` |

Nothing else takes `settings`: not the `decimals` functions, `model.make_item`,
`zones.clear_cache`, `rates.band`, `rates.base_price`, `packaging.fits`,
`packaging.total_weight`, `carriers.names`, `carriers.get`, nor the command
line's functions.

## 5. Nothing else changes

With a `Settings` holding the values `CONFIG` held, every function returns
exactly what it returns today, and raises the same errors (same classes, same
messages). The command line prints exactly what it prints today and keeps its
options (`--settings` now goes through `settings.load()`).

## 6. Calls are independent

Two calls with different `Settings` in the same process must not affect each
other, in any order, and without anyone calling `zones.clear_cache()` in
between: `lookup("10115", settings=us)` is 8 and
`lookup("10115", settings=eu)` is 1, whichever comes first and however often
they alternate. No module may keep state between calls that depends on the
settings of an earlier call. A cache is allowed only if its key includes every
setting its value depends on, so that it can never hand a value computed under
one `Settings` to a call made with another. There is no `global` statement
anywhere in the package.

## 7. Labels

`labels.render(shipment, units=..., settings=...)`: `units` overrides the
unit system for that one label, as today. An error part of the way through a
label (a blank recipient, a postcode of another region, items too big or too
heavy for a box) must leave nothing behind: the next call with the same
`Settings` behaves exactly as if the failed call had never happened. (Today it
does not: the override stays in `CONFIG`.)

## 8. `zones.clear_cache()`

Stays, and still takes no arguments.

## 9. Tests

Update the tests to the new API: keep every test file and every test (rewrite
them as you need, but do not delete any), no test may use `CONFIG`,
`load_config()` or `reset()`, and every test passes.

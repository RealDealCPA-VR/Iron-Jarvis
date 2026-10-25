# The inventory page: how it must behave

The page (`static/index.html` + `static/app.js`, served by `server.py`) shows every
item of `data/items.json` in the table `#items`, one row per item, in the data's order.

## Filters

* **Search** — a text box `<input id="search">` in the controls, above the table.
  As the user types (on every change of its text, not only on Enter), the table
  shows only the items whose SKU or name CONTAINS the text, ignoring upper/lower
  case. Spaces before and after the text are ignored; an empty box filters
  nothing.
* **Low stock only** — the checkbox `#low-stock`. When it is ticked, the table
  shows only the items whose quantity is AT OR BELOW their own reorder level
  (`qty <= reorder_at`). (Today it compares the quantity with 10, which is wrong.)
* The two filters combine: a row is shown when it passes both.

## What the page says

* `<p id="count">`, right under the controls, ALWAYS reads `Showing N of M items`:
  N is the number of rows the table shows now, M the number of items in the data
  (it says "items" even for 1). It is right as soon as the items have loaded, and
  after every change to either filter.
* `<p id="empty">No items match</p>` is shown when no row passes the filters, and
  hidden otherwise (the table then shows no rows).

Rows that do not pass the filters are either not in the table or hidden; either is
fine. Do not change `data/items.json`.

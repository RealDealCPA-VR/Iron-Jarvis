# inventory

The stock and sales book of a small hardware shop. Python 3.12, standard
library only.

| module                   | what it does                                                      |
| ------------------------ | ----------------------------------------------------------------- |
| `inventory/catalog.py`   | the products: SKU, name, stock unit, base price, reorder level    |
| `inventory/units.py`     | reads quantities like `"2 dz"` or `"1.5 kg"`, converts units      |
| `inventory/pricing.py`   | selling prices: base price plus the shop-wide markup              |
| `inventory/stock.py`     | `Stockroom`: stock levels, stock value, the reorder list          |
| `inventory/sales.py`     | `Order`: order lines, totals, shipping from a stockroom           |
| `inventory/report.py`    | the price list and a stockroom's stock report, as text            |

## Tests

    python -m unittest discover -s . -p "test_*.py"

CI runs the same tests in a random order. To run them the way CI does:

    python run_shuffled.py 7            # every test, in the order seed 7 gives
    python run_shuffled.py 1 2 3 4 5    # five orders, each in its own Python process

Each test must pass on its own, and the whole suite must pass in any order.

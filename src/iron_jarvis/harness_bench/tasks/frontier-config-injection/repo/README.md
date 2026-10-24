# shipping

Quotes, labels and notices for parcels. Python 3.12, standard library only.
Run the tests with:

    python -m unittest discover -s . -p "test_*.py"

| Module | What it does | Settings it reads today |
|---|---|---|
| `shipping/settings.py` | `CONFIG`, `DEFAULTS`, `load_config()`, `reset()`, `SettingsError` | — |
| `shipping/decimals.py` | `to_decimal()`, `rounded()` | none |
| `shipping/units.py` | weights and sizes as the user reads and writes them | `units` |
| `shipping/model.py` | `Item`, `Shipment`, `make_item()`, `shipment_from_dict()` | `units` (through `units.parse_weight`) |
| `shipping/zones.py` | postcode to zone, with a cache | `region` |
| `shipping/rates.py` | price by zone and weight | `currency`, `fuel_surcharge_pct` |
| `shipping/packaging.py` | the box for a shipment's items | `padding_cm`, `max_box_kg`, `units` (in an error message) |
| `shipping/carriers/` | `ground` and `express`: a `quote(shipment)` each; `names()`, `get()` | `region`, `currency`, and all of the above through the modules they call |
| `shipping/quote.py` | every carrier's quote, the cheapest, a summary | all of the above |
| `shipping/labels.py` | the shipping label (`units=` overrides the unit system) | `sender`, `region`, `units`, and the above |
| `shipping/notify.py` | the message for the recipient | `sender`, `units`, `currency`, and the above |
| `shipping/cli.py` | the command line | loads the settings file |

The settings (`settings.DEFAULTS` holds the defaults):

| Setting | Default | Allowed |
|---|---|---|
| `region` | `"US"` | `"US"`, `"EU"` |
| `units` | `"metric"` | `"metric"`, `"imperial"` |
| `currency` | `"USD"` | `"USD"`, `"EUR"`, `"GBP"` |
| `fuel_surcharge_pct` | `0` | a whole number 0 to 50 |
| `max_box_kg` | `20` | a number (whole or not) 1 to 70 |
| `padding_cm` | `2` | a whole number 0 to 10 |
| `sender` | `"Iron Shipping Co."` | text of 1 to 40 characters (spaces at either end not counted) |

The command line:

    python -m shipping.cli quote ORDER.json [--settings SETTINGS.json]
    python -m shipping.cli label ORDER.json [--settings SETTINGS.json] [--units metric|imperial]

An order is a JSON object: `{"recipient": "Ada Lovelace", "postcode": "10115",
"items": [{"name": "book", "dims_cm": [24, 17, 3], "weight": "0.9 kg"}]}`. A
settings file is a JSON object holding any of the settings above.

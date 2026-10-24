# IJ-BENCH-HIDDEN-CHECK frontier-config-injection
import ast
import dataclasses
import importlib
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DEFAULTS = {
    "region": "US",
    "units": "metric",
    "currency": "USD",
    "fuel_surcharge_pct": 0,
    "max_box_kg": 20,
    "padding_cm": 2,
    "sender": "Iron Shipping Co.",
}
SETTINGS_VALUES = {
    "US": {},
    "EU": {"region": "EU", "units": "imperial", "currency": "EUR", "fuel_surcharge_pct": 12,
           "max_box_kg": 15, "padding_cm": 4, "sender": "Paket Nord GmbH"},
    "GB": {"region": "EU", "units": "metric", "currency": "GBP", "fuel_surcharge_pct": 5,
           "max_box_kg": 30, "padding_cm": 0, "sender": "Parcel & Co"},
}
ORDERS = (
    ("US", "EU"),
    ("EU", "US"),
    ("GB", "US", "EU"),
    ("US", "US", "EU", "GB", "US", "EU", "GB", "EU", "US"),
)
ORDER = {"recipient": "Ada Lovelace", "postcode": "10115", "items": [
    {"name": "book", "dims_cm": [24, 17, 3], "weight": "0.9 kg"},
    {"name": "mug", "dims_cm": [12, 9, 7], "weight": 0.9},
    {"name": "tin", "dims_cm": [10, 10, 10], "weight": "2 LB"},
]}

# What the code returned before the refactor, per set of settings (computed on
# a fresh CONFIG and an empty zone cache for every call).
EXPECTED = {'US': {'weight_unit': 'kg',
        'weight_value': ('Decimal', '2.50'),
        'format_weight': '0.45 kg',
        'format_dims': '30.0 x 20.5 x 10.0 cm',
        'parse_weight_kg': ('Decimal', '2.5'),
        'parse_weight_lb': ('Decimal', '2.26796185'),
        'parse_weight_bare_text': ('Decimal', '3'),
        'parse_weight_bare_number': ('Decimal', '4'),
        'shipment_from_dict': ('Shipment',
                               'Ada Lovelace',
                               '10115',
                               [('Item',
                                 'book',
                                 [('Decimal', '24'), ('Decimal', '17'), ('Decimal', '3')],
                                 '0.9'),
                                ('Item',
                                 'mug',
                                 [('Decimal', '12'), ('Decimal', '9'), ('Decimal', '7')],
                                 '0.9'),
                                ('Item',
                                 'tin',
                                 [('Decimal', '10'), ('Decimal', '10'), ('Decimal', '10')],
                                 '0.90718474')]),
        'lookup_10115': 8,
        'lookup_94105': 1,
        'lookup_8001': ('raises', 'ZoneError', "not a postcode of region US: '8001'"),
        'lookup_zip4': 8,
        'lookup_spaces': 4,
        'price_1': ('Decimal', '5.20'),
        'price_8': ('Decimal', '30.80'),
        'price_5': ('Decimal', '40.70'),
        'format_price': 'USD 12.30',
        'needed_space_a': [('Decimal', '28'), ('Decimal', '21'), ('Decimal', '14')],
        'choose_box_a': ('Box', 'M', [('Decimal', '35'), ('Decimal', '25'), ('Decimal', '15')], '0.40'),
        'choose_box_b': ('Box', 'L', [('Decimal', '50'), ('Decimal', '40'), ('Decimal', '30')], '0.80'),
        'choose_box_heavy': ('Box', 'L', [('Decimal', '50'), ('Decimal', '40'), ('Decimal', '30')], '0.80'),
        'ground_a': ('Quote', 'Ground', 'standard', '12.50', 'USD', 9),
        'express_a': ('Quote', 'Express', 'two-day', '22.50', 'USD', 2),
        'ground_b': ('Quote', 'Ground', 'standard', '8.40', 'USD', 2),
        'express_c': ('raises', 'ZoneError', "not a postcode of region US: '8001'"),
        'quote_all_a': [('Quote', 'Ground', 'standard', '12.50', 'USD', 9),
                        ('Quote', 'Express', 'two-day', '22.50', 'USD', 2)],
        'quote_all_heavy': [('Quote', 'Ground', 'standard', '21.60', 'USD', 5),
                            ('Quote', 'Express', 'next-day', '38.88', 'USD', 1)],
        'cheapest_b': ('Quote', 'Ground', 'standard', '8.40', 'USD', 2),
        'summary_a': 'Quotes for Ada Lovelace (10115, zone 8):\n'
                     '  Ground   standard   USD 12.50  9 days\n'
                     '  Express  two-day    USD 22.50  2 days',
        'summary_c': ('raises', 'ZoneError', "not a postcode of region US: '8001'"),
        'render_a': 'FROM:   Iron Shipping Co.\n'
                    'TO:     Ada Lovelace\n'
                    'ZONE:   8 (10115, US)\n'
                    'BOX:    M, 35.0 x 25.0 x 15.0 cm\n'
                    'WEIGHT: 1.70 kg',
        'render_a_imperial': 'FROM:   Iron Shipping Co.\n'
                             'TO:     Ada Lovelace\n'
                             'ZONE:   8 (10115, US)\n'
                             'BOX:    M, 13.8 x 9.8 x 5.9 in\n'
                             'WEIGHT: 3.75 lb',
        'render_b_metric': 'FROM:   Iron Shipping Co.\n'
                           'TO:     Grace Hopper\n'
                           'ZONE:   1 (94105, US)\n'
                           'BOX:    L, 50.0 x 40.0 x 30.0 cm\n'
                           'WEIGHT: 4.20 kg',
        'render_c': ('raises', 'ZoneError', "not a postcode of region US: '8001'"),
        'render_heavy': 'FROM:   Iron Shipping Co.\n'
                        'TO:     Hedy Lamarr\n'
                        'ZONE:   4 (60311, US)\n'
                        'BOX:    L, 50.0 x 40.0 x 30.0 cm\n'
                        'WEIGHT: 15.30 kg',
        'message_b': 'Hello Grace Hopper, Iron Shipping Co. has sent your parcel (4.20 kg) with Express '
                     '(next-day). It cost USD 19.60 and should arrive in 1 day.',
        'message_heavy': 'Hello Hedy Lamarr, Iron Shipping Co. has sent your parcel (15.30 kg) with Express '
                         '(next-day). It cost USD 19.60 and should arrive in 1 day.'},
 'EU': {'weight_unit': 'lb',
        'weight_value': ('Decimal', '5.51'),
        'format_weight': '1.00 lb',
        'format_dims': '11.8 x 8.1 x 3.9 in',
        'parse_weight_kg': ('Decimal', '2.5'),
        'parse_weight_lb': ('Decimal', '2.26796185'),
        'parse_weight_bare_text': ('Decimal', '1.36077711'),
        'parse_weight_bare_number': ('Decimal', '1.81436948'),
        'shipment_from_dict': ('Shipment',
                               'Ada Lovelace',
                               '10115',
                               [('Item',
                                 'book',
                                 [('Decimal', '24'), ('Decimal', '17'), ('Decimal', '3')],
                                 '0.9'),
                                ('Item',
                                 'mug',
                                 [('Decimal', '12'), ('Decimal', '9'), ('Decimal', '7')],
                                 '0.408233133'),
                                ('Item',
                                 'tin',
                                 [('Decimal', '10'), ('Decimal', '10'), ('Decimal', '10')],
                                 '0.90718474')]),
        'lookup_10115': 1,
        'lookup_94105': 4,
        'lookup_8001': 4,
        'lookup_zip4': ('raises', 'ZoneError', "not a postcode of region EU: '12345-6789'"),
        'lookup_spaces': 3,
        'price_1': ('Decimal', '5.36'),
        'price_8': ('Decimal', '31.74'),
        'price_5': ('Decimal', '41.94'),
        'format_price': 'EUR 12.30',
        'needed_space_a': [('Decimal', '32'), ('Decimal', '25'), ('Decimal', '18')],
        'choose_box_a': ('Box', 'L', [('Decimal', '50'), ('Decimal', '40'), ('Decimal', '30')], '0.80'),
        'choose_box_b': ('Box', 'L', [('Decimal', '50'), ('Decimal', '40'), ('Decimal', '30')], '0.80'),
        'choose_box_heavy': ('raises',
                             'PackagingError',
                             'too heavy for one box: 33.73 lb (the limit is 33.07 lb)'),
        'ground_a': ('Quote', 'Ground', 'standard', '7.11', 'EUR', 3),
        'express_a': ('Quote', 'Express', 'next-day', '12.80', 'EUR', 1),
        'ground_b': ('Quote', 'Ground', 'standard', '11.13', 'EUR', 6),
        'express_c': ('Quote', 'Express', 'next-day', '16.13', 'EUR', 1),
        'quote_all_a': [('Quote', 'Ground', 'standard', '7.11', 'EUR', 3),
                        ('Quote', 'Express', 'next-day', '12.80', 'EUR', 1)],
        'quote_all_heavy': ('raises',
                            'PackagingError',
                            'too heavy for one box: 33.73 lb (the limit is 33.07 lb)'),
        'cheapest_b': ('Quote', 'Ground', 'standard', '11.13', 'EUR', 6),
        'summary_a': 'Quotes for Ada Lovelace (10115, zone 1):\n'
                     '  Ground   standard    EUR 7.11  3 days\n'
                     '  Express  next-day   EUR 12.80  1 day',
        'summary_c': 'Quotes for Carl Gauss (8001, zone 4):\n'
                     '  Ground   standard    EUR 8.96  6 days\n'
                     '  Express  next-day   EUR 16.13  1 day',
        'render_a': 'FROM:   Paket Nord GmbH\n'
                    'TO:     Ada Lovelace\n'
                    'ZONE:   1 (10115, EU)\n'
                    'BOX:    L, 19.7 x 15.7 x 11.8 in\n'
                    'WEIGHT: 4.63 lb',
        'render_a_imperial': 'FROM:   Paket Nord GmbH\n'
                             'TO:     Ada Lovelace\n'
                             'ZONE:   1 (10115, EU)\n'
                             'BOX:    L, 19.7 x 15.7 x 11.8 in\n'
                             'WEIGHT: 4.63 lb',
        'render_b_metric': 'FROM:   Paket Nord GmbH\n'
                           'TO:     Grace Hopper\n'
                           'ZONE:   4 (94105, EU)\n'
                           'BOX:    L, 50.0 x 40.0 x 30.0 cm\n'
                           'WEIGHT: 4.20 kg',
        'render_c': 'FROM:   Paket Nord GmbH\n'
                    'TO:     Carl Gauss\n'
                    'ZONE:   4 (8001, EU)\n'
                    'BOX:    L, 19.7 x 15.7 x 11.8 in\n'
                    'WEIGHT: 4.63 lb',
        'render_heavy': ('raises',
                         'PackagingError',
                         'too heavy for one box: 33.73 lb (the limit is 33.07 lb)'),
        'message_b': 'Hello Grace Hopper, Paket Nord GmbH has sent your parcel (9.26 lb) with Express '
                     '(next-day). It cost EUR 19.60 and should arrive in 1 day.',
        'message_heavy': ('raises',
                          'PackagingError',
                          'too heavy for one box: 33.73 lb (the limit is 33.07 lb)')},
 'GB': {'weight_unit': 'kg',
        'weight_value': ('Decimal', '2.50'),
        'format_weight': '0.45 kg',
        'format_dims': '30.0 x 20.5 x 10.0 cm',
        'parse_weight_kg': ('Decimal', '2.5'),
        'parse_weight_lb': ('Decimal', '2.26796185'),
        'parse_weight_bare_text': ('Decimal', '3'),
        'parse_weight_bare_number': ('Decimal', '4'),
        'shipment_from_dict': ('Shipment',
                               'Ada Lovelace',
                               '10115',
                               [('Item',
                                 'book',
                                 [('Decimal', '24'), ('Decimal', '17'), ('Decimal', '3')],
                                 '0.9'),
                                ('Item',
                                 'mug',
                                 [('Decimal', '12'), ('Decimal', '9'), ('Decimal', '7')],
                                 '0.9'),
                                ('Item',
                                 'tin',
                                 [('Decimal', '10'), ('Decimal', '10'), ('Decimal', '10')],
                                 '0.90718474')]),
        'lookup_10115': 1,
        'lookup_94105': 4,
        'lookup_8001': 4,
        'lookup_zip4': ('raises', 'ZoneError', "not a postcode of region EU: '12345-6789'"),
        'lookup_spaces': 3,
        'price_1': ('Decimal', '4.31'),
        'price_8': ('Decimal', '25.55'),
        'price_5': ('Decimal', '33.76'),
        'format_price': 'GBP 12.30',
        'needed_space_a': [('Decimal', '24'), ('Decimal', '17'), ('Decimal', '10')],
        'choose_box_a': ('Box', 'M', [('Decimal', '35'), ('Decimal', '25'), ('Decimal', '15')], '0.40'),
        'choose_box_b': ('Box', 'L', [('Decimal', '50'), ('Decimal', '40'), ('Decimal', '30')], '0.80'),
        'choose_box_heavy': ('Box', 'L', [('Decimal', '50'), ('Decimal', '40'), ('Decimal', '30')], '0.80'),
        'ground_a': ('Quote', 'Ground', 'standard', '5.72', 'GBP', 3),
        'express_a': ('Quote', 'Express', 'next-day', '10.30', 'GBP', 1),
        'ground_b': ('Quote', 'Ground', 'standard', '8.96', 'GBP', 6),
        'express_c': ('Quote', 'Express', 'next-day', '13.00', 'GBP', 1),
        'quote_all_a': [('Quote', 'Ground', 'standard', '5.72', 'GBP', 3),
                        ('Quote', 'Express', 'next-day', '10.30', 'GBP', 1)],
        'quote_all_heavy': [('Quote', 'Ground', 'standard', '16.42', 'GBP', 5),
                            ('Quote', 'Express', 'next-day', '29.56', 'GBP', 1)],
        'cheapest_b': ('Quote', 'Ground', 'standard', '8.96', 'GBP', 6),
        'summary_a': 'Quotes for Ada Lovelace (10115, zone 1):\n'
                     '  Ground   standard    GBP 5.72  3 days\n'
                     '  Express  next-day   GBP 10.30  1 day',
        'summary_c': 'Quotes for Carl Gauss (8001, zone 4):\n'
                     '  Ground   standard    GBP 7.22  6 days\n'
                     '  Express  next-day   GBP 13.00  1 day',
        'render_a': 'FROM:   Parcel & Co\n'
                    'TO:     Ada Lovelace\n'
                    'ZONE:   1 (10115, EU)\n'
                    'BOX:    M, 35.0 x 25.0 x 15.0 cm\n'
                    'WEIGHT: 1.70 kg',
        'render_a_imperial': 'FROM:   Parcel & Co\n'
                             'TO:     Ada Lovelace\n'
                             'ZONE:   1 (10115, EU)\n'
                             'BOX:    M, 13.8 x 9.8 x 5.9 in\n'
                             'WEIGHT: 3.75 lb',
        'render_b_metric': 'FROM:   Parcel & Co\n'
                           'TO:     Grace Hopper\n'
                           'ZONE:   4 (94105, EU)\n'
                           'BOX:    L, 50.0 x 40.0 x 30.0 cm\n'
                           'WEIGHT: 4.20 kg',
        'render_c': 'FROM:   Parcel & Co\n'
                    'TO:     Carl Gauss\n'
                    'ZONE:   4 (8001, EU)\n'
                    'BOX:    M, 35.0 x 25.0 x 15.0 cm\n'
                    'WEIGHT: 1.70 kg',
        'render_heavy': 'FROM:   Parcel & Co\n'
                        'TO:     Hedy Lamarr\n'
                        'ZONE:   3 (60311, EU)\n'
                        'BOX:    L, 50.0 x 40.0 x 30.0 cm\n'
                        'WEIGHT: 15.30 kg',
        'message_b': 'Hello Grace Hopper, Parcel & Co has sent your parcel (4.20 kg) with Express '
                     '(next-day). It cost GBP 19.60 and should arrive in 1 day.',
        'message_heavy': 'Hello Hedy Lamarr, Parcel & Co has sent your parcel (15.30 kg) with Express '
                         '(next-day). It cost GBP 19.60 and should arrive in 1 day.'}}

# What the command line printed before the refactor.
CLI_EXPECTED = {'quote_default': 'Quotes for Ada Lovelace (10115, zone 8):\n'
                  '  Ground   standard   USD 15.40  9 days\n'
                  '  Express  two-day    USD 27.72  2 days\n'
                  '\n'
                  'Hello Ada Lovelace, Iron Shipping Co. has sent your parcel (3.51 kg) with Ground '
                  '(standard). It cost USD 15.40 and should arrive in 9 days.\n',
 'quote_eu': 'Quotes for Ada Lovelace (10115, zone 1):\n'
             '  Ground   standard    EUR 8.66  3 days\n'
             '  Express  next-day   EUR 15.59  1 day\n'
             '\n'
             'Hello Ada Lovelace, Paket Nord GmbH has sent your parcel (6.65 lb) with Ground (standard). It '
             'cost EUR 8.66 and should arrive in 3 days.\n',
 'label_eu': 'FROM:   Paket Nord GmbH\n'
             'TO:     Ada Lovelace\n'
             'ZONE:   1 (10115, EU)\n'
             'BOX:    L, 19.7 x 15.7 x 11.8 in\n'
             'WEIGHT: 6.65 lb\n',
 'label_eu_metric': 'FROM:   Paket Nord GmbH\n'
                    'TO:     Ada Lovelace\n'
                    'ZONE:   1 (10115, EU)\n'
                    'BOX:    L, 50.0 x 40.0 x 30.0 cm\n'
                    'WEIGHT: 3.02 kg\n',
 'label_default_imperial': 'FROM:   Iron Shipping Co.\n'
                           'TO:     Ada Lovelace\n'
                           'ZONE:   8 (10115, US)\n'
                           'BOX:    L, 19.7 x 15.7 x 11.8 in\n'
                           'WEIGHT: 7.73 lb\n'}

POK = inspect.Parameter.POSITIONAL_OR_KEYWORD
EMPTY = inspect.Parameter.empty
# Functions that must take a keyword-only, required ``settings``, with their
# other parameters as they were: (name, kind, default).
TAKES_SETTINGS = {
    "units.weight_unit": [],
    "units.weight_value": [("kg", POK, EMPTY)],
    "units.format_weight": [("kg", POK, EMPTY)],
    "units.format_dims": [("dims_cm", POK, EMPTY)],
    "units.parse_weight": [("value", POK, EMPTY)],
    "model.shipment_from_dict": [("data", POK, EMPTY)],
    "zones.lookup": [("postcode", POK, EMPTY)],
    "rates.price": [("zone", POK, EMPTY), ("weight_kg", POK, EMPTY)],
    "rates.format_price": [("amount", POK, EMPTY)],
    "packaging.needed_space": [("items", POK, EMPTY)],
    "packaging.choose_box": [("items", POK, EMPTY)],
    "carriers.ground.quote": [("shipment", POK, EMPTY)],
    "carriers.express.quote": [("shipment", POK, EMPTY)],
    "quote.quote_all": [("shipment", POK, EMPTY)],
    "quote.cheapest": [("shipment", POK, EMPTY)],
    "quote.summary": [("shipment", POK, EMPTY)],
    "labels.render": [("shipment", POK, EMPTY), ("units", POK, None)],
    "notify.message": [("shipment", POK, EMPTY), ("quote", POK, EMPTY)],
}
# Functions that must NOT take ``settings``, unchanged.
NO_SETTINGS = {
    "decimals.to_decimal": [("value", POK, EMPTY)],
    "decimals.rounded": [("value", POK, EMPTY), ("places", POK, EMPTY)],
    "model.make_item": [("name", POK, EMPTY), ("dims_cm", POK, EMPTY), ("weight_kg", POK, EMPTY)],
    "zones.clear_cache": [],
    "rates.band": [("weight_kg", POK, EMPTY)],
    "rates.base_price": [("zone", POK, EMPTY), ("weight_kg", POK, EMPTY)],
    "packaging.fits": [("box", POK, EMPTY), ("space", POK, EMPTY)],
    "packaging.total_weight": [("items", POK, EMPTY), ("box", POK, None)],
    "carriers.names": [],
    "carriers.get": [("name", POK, EMPTY)],
}
# Every test file the project had, and how many tests it held.
TEST_FILES = {
    "test_settings.py": 5, "test_units.py": 4, "test_zones.py": 5, "test_rates.py": 4,
    "test_packaging.py": 4, "test_carriers.py": 3, "test_labels.py": 2, "test_notify.py": 1,
    "test_quote.py": 2, "test_cli.py": 3,
}


def _module(name):
    return importlib.import_module("shipping." + name)


def _function(dotted):
    module, _, name = dotted.rpartition(".")
    return getattr(_module(module), name)


def _settings(key):
    return _module("settings").Settings(**SETTINGS_VALUES[key])


def norm(value):
    if isinstance(value, Decimal):
        return ("Decimal", str(value))
    if isinstance(value, (list, tuple)):
        return [norm(v) for v in value]
    name = type(value).__name__
    if name == "Box":
        return ("Box", value.name, norm(value.inner_cm), str(value.tare_kg))
    if name == "Quote":
        return ("Quote", value.carrier, value.service, str(value.price), value.currency, value.days)
    if name == "Shipment":
        return ("Shipment", value.recipient, value.postcode, norm(value.items))
    if name == "Item":
        return ("Item", value.name, norm(value.dims_cm), str(value.weight_kg))
    return value


def run(fn, *args, **kwargs):
    try:
        return norm(fn(*args, **kwargs))
    except Exception as exc:  # noqa: BLE001
        return ("raises", type(exc).__name__, str(exc))


def cases():
    """(label, function, args, kwargs) for every call the expectations cover."""
    model = _module("model")
    make = model.make_item
    items_a = (make("book", (24, 17, 3), "0.9"), make("mug", (12, 9, 7), "0.4"))
    a = model.Shipment("Ada Lovelace", "10115", items_a)
    b = model.Shipment("Grace Hopper", "94105", (
        make("laptop", (36, 25, 3), "2.1"), make("charger", (12, 6, 4), "0.5"), make("bag", (40, 30, 8), "0.8"),
    ))
    c = model.Shipment("Carl Gauss", "8001", items_a)
    heavy = model.Shipment("Hedy Lamarr", "60311", (make("kettlebell", (20, 20, 20), "14.5"),))
    fixed = _module("carriers.base").Quote("Express", "next-day", Decimal("19.6"), "EUR", 1)
    units, zones, rates, packaging = _module("units"), _module("zones"), _module("rates"), _module("packaging")
    ground, express, quote = _module("carriers.ground"), _module("carriers.express"), _module("quote")
    labels, notify = _module("labels"), _module("notify")
    return [
        ("weight_unit", units.weight_unit, (), {}),
        ("weight_value", units.weight_value, (Decimal("2.5"),), {}),
        ("format_weight", units.format_weight, (Decimal("0.4536"),), {}),
        ("format_dims", units.format_dims, ((30, Decimal("20.5"), 10),), {}),
        ("parse_weight_kg", units.parse_weight, ("2.5 kg",), {}),
        ("parse_weight_lb", units.parse_weight, (" 5 LB ",), {}),
        ("parse_weight_bare_text", units.parse_weight, ("3",), {}),
        ("parse_weight_bare_number", units.parse_weight, (4,), {}),
        ("shipment_from_dict", model.shipment_from_dict, (json.loads(json.dumps(ORDER)),), {}),
        ("lookup_10115", zones.lookup, ("10115",), {}),
        ("lookup_94105", zones.lookup, ("94105",), {}),
        ("lookup_8001", zones.lookup, ("8001",), {}),
        ("lookup_zip4", zones.lookup, ("12345-6789",), {}),
        ("lookup_spaces", zones.lookup, (" 60311 ",), {}),
        ("price_1", rates.price, (1, Decimal("0.5")), {}),
        ("price_8", rates.price, (8, Decimal("12")), {}),
        ("price_5", rates.price, (5, Decimal("69.99")), {}),
        ("format_price", rates.format_price, (Decimal("12.3"),), {}),
        ("needed_space_a", packaging.needed_space, (a.items,), {}),
        ("choose_box_a", packaging.choose_box, (a.items,), {}),
        ("choose_box_b", packaging.choose_box, (b.items,), {}),
        ("choose_box_heavy", packaging.choose_box, (heavy.items,), {}),
        ("ground_a", ground.quote, (a,), {}),
        ("express_a", express.quote, (a,), {}),
        ("ground_b", ground.quote, (b,), {}),
        ("express_c", express.quote, (c,), {}),
        ("quote_all_a", quote.quote_all, (a,), {}),
        ("quote_all_heavy", quote.quote_all, (heavy,), {}),
        ("cheapest_b", quote.cheapest, (b,), {}),
        ("summary_a", quote.summary, (a,), {}),
        ("summary_c", quote.summary, (c,), {}),
        ("render_a", labels.render, (a,), {}),
        ("render_a_imperial", labels.render, (a,), {"units": "imperial"}),
        ("render_b_metric", labels.render, (b,), {"units": "metric"}),
        ("render_c", labels.render, (c,), {}),
        ("render_heavy", labels.render, (heavy,), {}),
        ("message_b", notify.message, (b, fixed), {}),
        ("message_heavy", notify.message, (heavy, fixed), {}),
    ]


class HiddenSettings(unittest.TestCase):
    def test_settings_is_a_frozen_dataclass_holding_the_defaults(self):
        Settings = _module("settings").Settings
        self.assertTrue(dataclasses.is_dataclass(Settings))
        self.assertTrue(Settings.__dataclass_params__.frozen, "Settings must be a frozen dataclass")
        self.assertEqual([f.name for f in dataclasses.fields(Settings)], list(DEFAULTS))
        default = Settings()
        self.assertEqual({f.name: getattr(default, f.name) for f in dataclasses.fields(Settings)}, DEFAULTS)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            default.region = "EU"
        self.assertEqual(default.region, "US")
        self.assertEqual(hash(Settings()), hash(Settings()))
        eu = _settings("EU")
        self.assertEqual((eu.region, eu.units, eu.sender), ("EU", "imperial", "Paket Nord GmbH"))
        self.assertEqual(Settings(max_box_kg=12.5).max_box_kg, 12.5)

    def test_bad_values_raise_settings_error(self):
        settings = _module("settings")
        self.assertTrue(issubclass(settings.SettingsError, ValueError))
        bad = [
            {"region": "XX"}, {"region": "eu"}, {"units": "IMPERIAL"}, {"currency": "JPY"},
            {"fuel_surcharge_pct": 51}, {"fuel_surcharge_pct": -1}, {"fuel_surcharge_pct": True},
            {"fuel_surcharge_pct": 1.5}, {"max_box_kg": 0}, {"max_box_kg": 71}, {"max_box_kg": "20"},
            {"max_box_kg": False}, {"padding_cm": -1}, {"padding_cm": 11}, {"padding_cm": 2.0},
            {"sender": ""}, {"sender": "   "}, {"sender": "x" * 41}, {"sender": 5},
        ]
        for values in bad:
            with self.subTest(values=values):
                with self.assertRaises(settings.SettingsError):
                    settings.Settings(**values)

    def test_load(self):
        settings = _module("settings")
        with tempfile.TemporaryDirectory() as tmp:
            def write(text):
                path = os.path.join(tmp, "s.json")
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(text)
                return path

            loaded = settings.load(write(json.dumps({"region": "EU", "fuel_surcharge_pct": 12})))
            self.assertIsInstance(loaded, settings.Settings)
            self.assertEqual(loaded, settings.Settings(region="EU", fuel_surcharge_pct=12))
            self.assertEqual(settings.load(write(json.dumps(SETTINGS_VALUES["EU"]))), _settings("EU"))
            self.assertEqual(settings.load(write("{}")), settings.Settings())
            for text in ('{"colour": "blue"}', "[1, 2]", "{not json", '{"region": "Mars"}', '"US"'):
                with self.subTest(text=text):
                    with self.assertRaises(settings.SettingsError):
                        settings.load(write(text))

    def test_the_global_is_gone(self):
        package = Path(HERE, "shipping")
        modules = []
        for path in sorted(package.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            parts = path.relative_to(HERE).with_suffix("").parts
            if parts[-1] == "__init__":
                parts = parts[:-1]
            modules.append(".".join(parts))
            tree = ast.parse(path.read_text(encoding="utf-8"))
            globals_used = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Global)]
            self.assertEqual(globals_used, [], f"{path.name} has a global statement")
        self.assertIn("shipping.settings", modules)
        for name in modules:
            module = importlib.import_module(name)
            self.assertFalse(hasattr(module, "CONFIG"), f"{name} still has CONFIG")
        settings = _module("settings")
        self.assertFalse(hasattr(settings, "load_config"))
        self.assertFalse(hasattr(settings, "reset"))


class HiddenSignatures(unittest.TestCase):
    def _others(self, fn):
        return [(p.name, p.kind, p.default) for p in inspect.signature(fn).parameters.values() if p.name != "settings"]

    def test_the_functions_that_need_settings_take_it(self):
        for dotted, others in TAKES_SETTINGS.items():
            with self.subTest(function=dotted):
                params = inspect.signature(_function(dotted)).parameters
                self.assertIn("settings", params)
                self.assertEqual(params["settings"].kind, inspect.Parameter.KEYWORD_ONLY)
                self.assertIs(params["settings"].default, EMPTY, "settings must be required")
                self.assertEqual(self._others(_function(dotted)), others)

    def test_the_others_do_not(self):
        for dotted, params in NO_SETTINGS.items():
            with self.subTest(function=dotted):
                self.assertNotIn("settings", inspect.signature(_function(dotted)).parameters)
                self.assertEqual(self._others(_function(dotted)), params)


class HiddenResults(unittest.TestCase):
    def check(self, key, label, fn, args, kwargs, where):
        got = run(fn, *args, **kwargs, settings=_settings(key))
        self.assertEqual(got, EXPECTED[key][label], f"{label} with the {key} settings ({where})")

    def test_results_have_not_changed(self):
        for key in SETTINGS_VALUES:
            for label, fn, args, kwargs in cases():
                self.check(key, label, fn, args, kwargs, "alone")

    def test_alternating_settings_call_by_call(self):
        for order in ORDERS:
            for label, fn, args, kwargs in cases():
                for key in order:
                    self.check(key, label, fn, args, kwargs, f"call by call, order {order}")

    def test_alternating_settings_round_by_round(self):
        for order in ORDERS:
            for key in order:
                for label, fn, args, kwargs in cases():
                    self.check(key, label, fn, args, kwargs, f"round by round, order {order}")

    def test_the_same_postcode_in_two_regions(self):
        zones = _module("zones")
        us, eu = _settings("US"), _settings("EU")
        for _ in range(3):
            self.assertEqual(zones.lookup("10115", settings=us), 8)
            self.assertEqual(zones.lookup("10115", settings=eu), 1)
        self.assertEqual(zones.lookup("8001", settings=eu), 4)
        with self.assertRaises(zones.ZoneError):
            zones.lookup("8001", settings=us)
        self.assertEqual(zones.lookup("8001", settings=eu), 4)

    def test_a_cleared_cache_changes_nothing(self):
        zones = _module("zones")
        us, eu = _settings("US"), _settings("EU")
        self.assertEqual(zones.lookup("94105", settings=eu), 4)
        zones.clear_cache()
        self.assertEqual(zones.lookup("94105", settings=us), 1)
        self.assertEqual(zones.lookup("94105", settings=eu), 4)


class HiddenLabels(unittest.TestCase):
    def test_a_failed_label_leaves_nothing_behind(self):
        labels, model, zones, packaging = _module("labels"), _module("model"), _module("zones"), _module("packaging")
        units = _module("units")
        us = _settings("US")
        items = (model.make_item("book", (24, 17, 3), "0.9"), model.make_item("mug", (12, 9, 7), "0.4"))
        ada = model.Shipment("Ada Lovelace", "10115", items)
        failures = [
            (model.Shipment("   ", "10115", items), us, labels.LabelError),
            (model.Shipment("Ada Lovelace", "8001", items), us, zones.ZoneError),
            (model.Shipment("Hedy Lamarr", "60311", (model.make_item("k", (20, 20, 20), "14.5"),)),
             _module("settings").Settings(max_box_kg=15), packaging.PackagingError),
            (model.Shipment("Ada Lovelace", "10115", (model.make_item("ski", (180, 10, 10), "3"),)), us,
             packaging.PackagingError),
        ]
        for shipment, settings, error in failures:
            with self.subTest(error=error.__name__, recipient=shipment.recipient):
                with self.assertRaises(error):
                    labels.render(shipment, units="imperial", settings=settings)
                self.assertEqual(labels.render(ada, settings=us), EXPECTED["US"]["render_a"])
                self.assertEqual(units.format_weight(Decimal("2.5"), settings=us), "2.50 kg")
                self.assertEqual(units.format_weight(Decimal("2.5"), settings=settings), "2.50 kg")
                self.assertEqual(settings.units, "metric")

    def test_an_override_does_not_touch_the_settings_given(self):
        labels, model = _module("labels"), _module("model")
        us = _settings("US")
        ada = model.Shipment("Ada Lovelace", "10115", (model.make_item("book", (24, 17, 3), "0.9"),
                                                         model.make_item("mug", (12, 9, 7), "0.4")))
        self.assertEqual(labels.render(ada, units="imperial", settings=us), EXPECTED["US"]["render_a_imperial"])
        self.assertEqual(us, _module("settings").Settings())
        self.assertEqual(labels.render(ada, settings=us), EXPECTED["US"]["render_a"])
        self.assertEqual(labels.render(ada, "imperial", settings=us), EXPECTED["US"]["render_a_imperial"])


class HiddenCli(unittest.TestCase):
    def _run(self, *args):
        env = dict(os.environ)
        env.pop("PYTHONSAFEPATH", None)
        env["PYTHONPATH"] = HERE
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run([sys.executable, "-m", "shipping.cli", *args], cwd=HERE, env=env,
                              capture_output=True, text=True, encoding="utf-8", timeout=120)

    def test_the_command_line_prints_what_it_printed(self):
        with tempfile.TemporaryDirectory() as tmp:
            order = os.path.join(tmp, "order.json")
            with open(order, "w", encoding="utf-8") as fh:
                json.dump(ORDER, fh)
            eu = os.path.join(tmp, "eu.json")
            with open(eu, "w", encoding="utf-8") as fh:
                json.dump(SETTINGS_VALUES["EU"], fh)
            bad = os.path.join(tmp, "bad.json")
            with open(bad, "w", encoding="utf-8") as fh:
                json.dump({"region": "Mars"}, fh)
            runs = {
                "quote_default": ["quote", order],
                "quote_eu": ["quote", order, "--settings", eu],
                "label_eu": ["label", order, "--settings", eu],
                "label_eu_metric": ["label", order, "--settings", eu, "--units", "metric"],
                "label_default_imperial": ["label", order, "--units", "imperial"],
            }
            for label, args in runs.items():
                with self.subTest(run=label):
                    done = self._run(*args)
                    self.assertEqual((done.returncode, done.stderr), (0, ""))
                    self.assertEqual(done.stdout, CLI_EXPECTED[label])
            done = self._run("quote", order, "--settings", bad)
            self.assertEqual((done.returncode, done.stdout), (1, ""))
            self.assertTrue(done.stderr.startswith("error: "), done.stderr)


class HiddenTestsKept(unittest.TestCase):
    def test_every_test_file_and_every_test_is_still_there(self):
        for name, count in TEST_FILES.items():
            with self.subTest(file=name):
                path = os.path.join(HERE, name)
                self.assertTrue(os.path.isfile(path), f"{name} is missing")
                with open(path, encoding="utf-8") as fh:
                    found = len(re.findall(r"^\s*def test_\w*\(", fh.read(), re.MULTILINE))
                self.assertGreaterEqual(found, count, f"{name} lost tests")


if __name__ == "__main__":
    unittest.main()

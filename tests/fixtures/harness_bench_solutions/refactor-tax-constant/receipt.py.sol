"""Receipts show the tax that is already inside a price."""

import config


def tax_part(gross):
    """The tax contained in a tax-included ``gross`` amount."""
    return round(gross - gross / (1 + config.TAX_RATE), 2)

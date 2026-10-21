"""Receipts show the tax that is already inside a price."""


def tax_part(gross):
    """The tax contained in a tax-included ``gross`` amount."""
    return round(gross - gross / 1.0825, 2)

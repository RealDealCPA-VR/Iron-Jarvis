Correct code (the reference's); test_pricing.py keeps every test of both branches by name
and every case, reached through an import alias, the pricing module itself and a table that
holds the functions (expected value first in assertEqual). The hidden check must recognise
a kept case however the test reaches the function.

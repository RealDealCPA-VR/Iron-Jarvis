Correct code (the reference's); test_pricing.py keeps every test of both branches by name
and every case and expected value, but writes the cases as tables run with subTest. Keeping
a test is keeping what it checks, not its layout.

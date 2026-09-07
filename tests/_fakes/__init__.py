"""Shared test fakes that are too big to live inside one test module.

``tests`` is a package, so a fake here is imported as
``from tests._fakes.browser_peer import BrowserPeer``. Nothing in this package is
importable from application code, and nothing in it may import a test module: a
fixture that reaches back into a test file couples two suites that are meant to be
independent, and the coupling only shows up when one of them is run alone.
"""

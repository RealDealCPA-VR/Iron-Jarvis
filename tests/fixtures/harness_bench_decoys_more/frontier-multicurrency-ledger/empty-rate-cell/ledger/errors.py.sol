"""The errors the ledger raises (SPEC.md, sections 1 and 7.1).

Every error raised on purpose is a LedgerError, so a caller can catch them
all at once (the command line does).
"""


class LedgerError(Exception):
    """Base class of every error this package raises on purpose."""


class ParseError(LedgerError):
    """A file could not be read: ``line 4: bad amount '1.234'``."""

    def __init__(self, line: int, problem: str) -> None:
        self.line = line
        self.problem = problem
        super().__init__(f"line {line}: {problem}")


class UnknownAccount(LedgerError):
    """A book has no account of that name."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"no such account {name!r}")


class CurrencyMismatch(LedgerError):
    """Two amounts in different currencies were added, subtracted or ordered."""

    def __init__(self, left: str, right: str) -> None:
        self.left = left
        self.right = right
        super().__init__(f"currency mismatch: {left} vs {right}")


class MissingRate(LedgerError):
    """No exchange rate converts ``currency`` into ``base``."""

    def __init__(self, currency: str, base: str) -> None:
        self.currency = currency
        self.base = base
        super().__init__(f"no exchange rate for {currency} (base {base})")

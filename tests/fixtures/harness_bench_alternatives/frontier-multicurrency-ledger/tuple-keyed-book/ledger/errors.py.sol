"""Every error the ledger raises on purpose (SPEC.md sections 1 and 7.1)."""


class LedgerError(Exception):
    """The base of every error raised on purpose."""


class ParseError(LedgerError):
    def __init__(self, line: int, problem: str) -> None:
        super().__init__(f"line {line}: {problem}")
        self.line, self.problem = line, problem


class UnknownAccount(LedgerError):
    def __init__(self, name: str) -> None:
        super().__init__(f"no such account {name!r}")
        self.name = name


class CurrencyMismatch(LedgerError):
    def __init__(self, left: str, right: str) -> None:
        super().__init__(f"currency mismatch: {left} vs {right}")
        self.left, self.right = left, right


class MissingRate(LedgerError):
    def __init__(self, currency: str, base: str) -> None:
        super().__init__(f"no exchange rate for {currency} (base {base})")
        self.currency, self.base = currency, base

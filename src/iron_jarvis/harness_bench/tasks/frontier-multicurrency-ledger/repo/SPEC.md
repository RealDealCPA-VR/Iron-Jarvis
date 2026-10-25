# ledger

`ledger` reads a CSV file of transactions and reports the balance of every
account. Python 3.12, standard library only. Run the tests with:

    python -m unittest discover -s . -p "test_*.py"

| Module | What it holds |
|---|---|
| `ledger/errors.py` | `LedgerError` and every error the package raises |
| `ledger/money.py` | `Money`, an exact amount of money |
| `ledger/parse.py` | `Transaction`, `parse_text()`, `read_transactions()` |
| `ledger/book.py` | `Book`: accounts and their balances |
| `ledger/rates.py` | exchange rates (empty today, see section 7.5) |
| `ledger/report.py` | `render()`: the plain-text report |
| `ledger/cli.py` | the command line |

Sections 1 to 6 describe the ledger as it is today: every amount is in US
dollars. Section 7 describes multi-currency support, which is not written yet.
Where section 7 says something different from sections 1 to 6, section 7 wins;
everything sections 1 to 6 say that section 7 does not change stays true.

## 1. Errors (`errors.py`)

Every error the package raises on purpose is a `LedgerError` (a subclass of
`Exception`):

- `ParseError(line, problem)`: a file could not be read. Attributes `line`
  (1 is the first line of the file) and `problem`; the message is
  `line <line>: <problem>`, for example `line 4: bad amount '1.234'`. A problem
  that quotes a cell shows the cell's text as Python's `repr()` shows it
  (`'1.234'`).
- `UnknownAccount(name)`: a book has no account of that name. Attribute
  `name`; message `no such account 'Nope'`.

## 2. Money (`money.py`)

`Money(amount=0)` is an exact amount of US dollars.

- `amount` is a `str`, an `int` or a `Decimal`, kept exactly as a `Decimal`
  (the `amount` property). A `float` or a `bool` raises `TypeError`; text that
  is not a number raises `ValueError`.
- `Money.parse(text)` reads an amount as written in a transactions file: an
  optional minus sign, digits, and at most two decimal places (`12.50`, `-3`,
  `0.5`). Surrounding spaces are ignored. Anything else raises `ValueError`.
- `Money.zero()` is `Money(0)`.
- `+` and `-` between two `Money`, and unary `-`, give a new `Money`, exactly
  (no rounding).
- `==` compares amounts (`Money("1.5") == Money("1.50")`); `<`, `<=`, `>`,
  `>=` order by amount. Equal values have equal hashes.
- `format()` rounds to cents with ROUND_HALF_EVEN and writes the amount with a
  comma between thousands and two decimals: `1,234.50`, `-12.00`, and `0.12`
  for `0.125`. A value that rounds to zero is written `0.00`, never `-0.00`.
  `str(m)` is `m.format()`.

## 3. Transactions file (`parse.py`)

A transactions file is UTF-8 CSV (a byte-order mark is allowed).

- A row that is empty, holds only blank cells, or whose first cell starts with
  `#` is skipped, anywhere in the file (also before the header).
- The first row that is not skipped is the header. Column names are matched
  without regard to case or surrounding spaces, in any order. Required
  columns: `date`, `account`, `amount`. Optional column: `description`. A
  missing required column, an unknown column or a column named twice is a
  `ParseError` on the header's line: `missing column 'amount'`,
  `unknown column 'memo'`, `duplicate column 'date'`.
- Every later row is one transaction. Cells are stripped of surrounding
  spaces. A row shorter than the header is padded with empty cells; a longer
  one is an error `expected 3 cells, found 4`.
- The cells of a row are checked in this order (the first problem found is the
  one reported):
  - `date`: `YYYY-MM-DD` and a real date, else `bad date '2024-02-30'`;
  - `account`: any non-empty text, else `missing account`;
  - `amount`: what `Money.parse()` accepts, else `bad amount '1.234'`;
  - `description`: any text (empty when the column is absent).
- Line numbers are the file's physical line numbers (the header of a file
  without comments is line 1).
- A file with no header row (empty, or nothing but skipped rows) holds no
  transactions.

`Transaction` is a frozen dataclass with `date` (`datetime.date`), `account`
(`str`), `amount` (`Money`) and `description` (`str`, default `""`).
`parse_text(text)` parses CSV text; `read_transactions(path)` reads a file.
Both return a list of `Transaction` in file order.

## 4. Book (`book.py`)

`Book.from_transactions(transactions)` posts each transaction in turn
(`post(txn)` adds its amount to its account; an account exists once a
transaction has been posted to it).

- `accounts()`: the account names, sorted (plain string order).
- `balance(account)`: the account's balance as `Money`. An account that never
  had a transaction raises `UnknownAccount`.
- `total()`: the sum of all balances.

## 5. Report (`report.py`)

`render(book)` returns the report as text, every line ending in a newline:

    Ledger report
    =============

    Assets:Bank    USD  1,000.00
    Expenses:Food  USD    -12.00
    ----------------------------
    Total          USD    988.00

- Three head lines: the title `Ledger report`, a line of `=` as long as the
  title, an empty line.
- The table: one line per account in `accounts()` order (the label is the
  account name), a rule, then the `Total` line.
- Every line of the table is the label, padded with spaces on the right to the
  width of the longest label in the table, then two spaces, the three-letter
  currency code, two spaces, and the formatted amount right-aligned to the
  width of the longest amount in the table. A rule is a line of `-` exactly as
  long as a table line.
- A book with no accounts gives the three head lines and then
  `(no transactions)`.

## 6. Command line (`cli.py`)

    python -m ledger.cli report CSV
    python -m ledger.cli balance CSV ACCOUNT
    python -m ledger.cli accounts CSV

- `report` prints `render()` of the file's book.
- `balance` prints one line: the currency code, a space, the formatted
  balance (`USD 1,000.00`).
- `accounts` prints one account name per line, in `accounts()` order.
- Exit code 0 on success. A file that cannot be read, a `ParseError`, an
  unknown account: exactly one line on standard error, `error: ` followed by
  the problem (`error: line 3: bad amount '1.234'`), nothing on standard
  output, exit code 1. A usage error (argparse's own) exits with 2.

## 7. Multi-currency (to do)

Every amount gets a currency. A currency code is three ASCII letters. Codes
are not case-sensitive anywhere (in a file, on the command line, as an
argument to a function) and are always normalised to upper case: `eur`, `Eur`
and `EUR` are the same currency, `EUR`. US dollars (`USD`) stay the default
wherever no currency is given, so a file without a `currency` column gives
exactly the same results as before.

### 7.1 Errors

Two new errors in `errors.py`, both subclasses of `LedgerError`:

- `CurrencyMismatch(left, right)`: attributes `left` and `right` (the codes of
  the two operands, in order); message `currency mismatch: USD vs EUR`.
- `MissingRate(currency, base)`: attributes `currency` and `base`; message
  `no exchange rate for EUR (base USD)`.

### 7.2 Money

- `Money(amount=0, currency="USD")`, `Money.parse(text, currency="USD")` and
  `Money.zero(currency="USD")`. The `currency` property is the normalised
  code. A code that is not exactly three ASCII letters (`"EURO"`, `"E1R"`,
  `""`, `" EUR"`) raises `ValueError`.
- `+` and `-` between two `Money` of the same currency give that currency;
  unary `-` keeps it. Between two different currencies they raise
  `CurrencyMismatch(left.currency, right.currency)`.
- `<`, `<=`, `>`, `>=` between two different currencies raise
  `CurrencyMismatch` too.
- `==` and `!=` never raise: two `Money` are equal when both their currencies
  and their amounts are equal (`Money("1", "EUR") != Money("1", "USD")`,
  `Money("1.5", "eur") == Money("1.50", "EUR")`). Equal values have equal
  hashes.
- `format()` is unchanged (it does not show the code). `repr()` shows both:
  `Money('12.50', 'EUR')`.

### 7.3 Transactions file

- A new optional column `currency`. Its cell, stripped, is the transaction's
  currency, in any case (`eur`, `Eur` and `EUR` are all `EUR`). An empty cell,
  a missing cell in a short row, or no `currency` column at all means `USD`.
  Any other value (`EURO`, `E1R`, `€`) is an error `bad currency 'EURO'`.
- The currency cell is checked after the amount (order: date, account, amount,
  currency).
- `Transaction.amount` is a `Money` in the row's currency.

### 7.4 Book

Balances are kept per account per currency.

- `accounts()` is unchanged.
- `currencies()`: the codes of every currency ever posted to the book, sorted.
- `balances(account)`: a `dict` from currency code to `Money`, with one entry
  for every currency ever posted to that account (also when its balance is now
  zero), keys in code order. `UnknownAccount` for an unknown account.
- `balance(account, currency="USD")`: the account's balance in that currency
  (`currency` in any case); `Money.zero(currency)` when the account never had
  that currency; `UnknownAccount` for an unknown account.
- `totals()`: a `dict` from currency code to the sum of every account's balance
  in that currency, keys in code order.
- `total(currency="USD")`: that currency's entry of `totals()`, or
  `Money.zero(currency)` when there is none.

### 7.5 Exchange rates (`rates.py`)

A rates file is UTF-8 CSV (a byte-order mark is allowed) with the columns
`base`, `currency` and `rate`, read with the same rules as a transactions file
(header in any case and any order, a missing, unknown or duplicate column,
skipped rows, stripped cells, short and long rows, line numbers). Each row
says: one unit of `currency` is worth `rate` units of `base`.

    base,currency,rate
    USD,EUR,1.0850
    USD,GBP,1.2700

- `base` and `currency`: codes in any case; anything else is
  `bad currency '…'` (an empty cell too).
- `rate`: digits with an optional fractional part of any length (`1.0850`,
  `2`, `0.5`), greater than zero; anything else (`0`, `-1`, `1e3`, empty) is
  `bad rate '…'`.
- A second row for the same base and currency is an error
  `duplicate rate for EUR (base USD)`.
- The cells of a row are checked in the order base, currency, rate.

The API:

- `parse_rates(text)` and `load_rates(path)` return a `RateTable`.
- `RateTable.rate(base, currency)` returns the rate as a `Decimal` (codes in
  any case). The rate of a currency into itself is always `Decimal(1)`, with no
  row needed. A pair without a row raises `MissingRate(currency, base)`.
  A rate is only ever used as written: it is never inverted (a row
  `EUR,USD,0.9217` says nothing about base `USD`, currency `EUR`) and never
  chained through a third currency.
- `convert(money, base, table)` returns `money` in `base`: its amount times
  `table.rate(base, money.currency)`, rounded to cents with ROUND_HALF_EVEN
  (`0.025` becomes `0.02`, `0.035` becomes `0.04`, `-0.015` becomes `-0.02`).
  Money already in `base` is returned unchanged.

### 7.6 Report

`render(book, base=None, rates=None)`:

- The table has one line per account per currency: accounts in `accounts()`
  order, and within an account its currencies in code order (the order of
  `balances()`), whatever order they appear in in the file. The code column
  shows the line's currency.
- After the rule, one `Total` line per currency, in code order (`totals()`).
- Only when `base` is given (`rates` is then a `RateTable`): a second rule and
  one `Grand total` line, whose code column is the base code (normalised) and
  whose amount is `grand_total(book, base, rates)`.
- Widths (section 5) are computed over every line of the table, the
  `Grand total` line included.
- A book with no accounts still gives `(no transactions)`; no grand total is
  computed.

`grand_total(book, base, rates)` returns a `Money` in `base`:

1. Every currency of the book (`currencies()`) other than the base needs a
   rate, even a currency whose balances are all zero. If any has none, raise
   `MissingRate` for the first such currency in code order.
2. Convert every balance separately (each account, each currency) with
   `convert()`, which rounds each converted balance to cents (ROUND_HALF_EVEN),
   and only then add them up. Do not convert the per-currency totals, and do
   not round the sum: two accounts each holding `0.01` EUR at a rate of `1.5`
   add `0.02 + 0.02 = 0.04` USD to the grand total, not `0.03`.

With `examples/transactions.csv` and `examples/rates.csv`, the command
`python -m ledger.cli report examples/transactions.csv --base usd` prints:

    Ledger report
    =============

    Assets:Bank      EUR    200.00
    Assets:Bank      USD  1,000.00
    Assets:Wallet    GBP     30.00
    Expenses:Coffee  EUR     -3.00
    Expenses:Food    USD    -12.00
    Expenses:Snacks  EUR     -3.00
    Expenses:Travel  EUR    -46.00
    ------------------------------
    Total            EUR    148.00
    Total            GBP     30.00
    Total            USD    988.00
    ------------------------------
    Grand total      USD  1,186.67

(`-3.00` EUR is `-3.255` USD, so Coffee and Snacks each add `-3.26`; converting
the EUR total `148.00` instead would give `160.58` for the euros and a grand
total of `1,186.68`, which is wrong.) Without `--base` the report ends after
the `Total` lines.

### 7.7 Command line

    python -m ledger.cli report CSV [--base CUR] [--rates PATH]

- `--base CUR` (any case) adds the grand total. `--rates PATH` names the rates
  file; without it the rates file is `rates.csv` in the same folder as `CSV`.
  Without `--base` no rates file is read, and `--rates` is ignored. A `--base`
  that is not three letters is a usage error.
- A missing rate: exactly one line on standard error, `error: ` followed by the
  `MissingRate` message (`error: no exchange rate for EUR (base USD)`), nothing
  on standard output, and exit code **2**. Every other error keeps exit
  code 1 (a rates file that cannot be read or holds a `ParseError` too).
- `balance CSV ACCOUNT` prints one line per currency of the account, in code
  order: `EUR 200.00`, then `USD 1,000.00`.

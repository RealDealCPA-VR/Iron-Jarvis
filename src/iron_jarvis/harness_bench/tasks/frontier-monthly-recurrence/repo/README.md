# recur

Dates of repeating events: rent on the 31st, a weekly stand-up, the payroll
run on the last business day of each month. Python 3.12, standard library
only. Run the tests with:

    python -m unittest discover -s . -p "test_*.py"

## The contract

Every function below keeps these rules; the tests and the people who use this
package rely on them.

- **Dates are naive local dates**: `datetime.date` values, with no time and
  no time zone. A `datetime` is refused with `TypeError` (it is a subclass of
  `date`, so it must be checked for explicitly).
- **`count` is exact**: a rule returns exactly `count` dates, in increasing
  order. `count=0` gives an empty list; a negative `count` raises
  `ValueError`; a `count` that is not an `int` raises `TypeError`.
- **`interval`** (default 1) is how many months (or weeks) apart the dates
  are. An `interval` below 1 raises `ValueError`; one that is not an `int`
  raises `TypeError`.

### `monthly(start, count, interval=1)`

The first date is `start`; then one date every `interval` months.

A monthly series keeps the START date's day of month. In a month without that
day, it falls on the month's last day. The series never drifts: the 31st stays
the 31st whenever the month has one.

- A series starting January 31 falls on February 28 (February 29 in a leap
  year), then March 31, April 30, May 31, June 30, July 31, ...
- A series starting January 30 falls on February 28 (or 29), then March 30,
  April 30, May 30, ...
- It is the START date's day that counts, not "the last day of the month": a
  series starting February 28 stays on the 28th (March 28, April 28, ...), and
  one starting April 30 stays on the 30th (May 30, June 30, ...).

### `weekly(start, count, interval=1)`

The first date is `start`; then one date every `interval` weeks.

### `last_business_days(year, holidays=())`

The last business day of each of the 12 months of `year`, January first. A
business day is a Monday to Friday that is not one of `holidays` (any iterable
of dates). It is built on `monthly`: a series starting January 31 falls on
every month's last day, and from there it steps back over weekends and
holidays.

### `holidays_for(year)` and `observed(day)` (`recur/holidays.py`)

`holidays_for(year)` is the set of fixed holidays OBSERVED during `year`: a
holiday on a Saturday is observed on the Friday before, one on a Sunday on the
Monday after. So when New Year's Day falls on a Saturday it is observed on
Friday, December 31 of the year before — and belongs to that year's set.

### Export (`recur/export.py`)

`export_series(summary, dates)` writes iCalendar text with one all-day event
per date; `export_monthly(summary, start, count, interval=1)` does the same
for `monthly(start, count, interval)`. Dates are written as `YYYYMMDD`; the
dates must be strictly increasing (`ValueError` otherwise). Event `n`
(counting from 1) has the UID `<slug>-<n>@recur`, where the slug is the
summary in lower case with every run of other characters than `a-z` and `0-9`
replaced by `-`. Every line ends with CRLF (`\r\n`), as iCalendar requires.

`export_monthly("Rent", date(2025, 1, 31), 2)` is, line by line:

    BEGIN:VCALENDAR
    VERSION:2.0
    PRODID:-//recur//EN
    BEGIN:VEVENT
    UID:rent-1@recur
    DTSTART;VALUE=DATE:20250131
    SUMMARY:Rent
    END:VEVENT
    BEGIN:VEVENT
    UID:rent-2@recur
    DTSTART;VALUE=DATE:20250228
    SUMMARY:Rent
    END:VEVENT
    END:VCALENDAR

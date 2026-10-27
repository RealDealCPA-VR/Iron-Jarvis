"""Business days: Monday to Friday, except holidays."""

from datetime import timedelta


def is_business_day(day, holidays=()):
    return day.weekday() < 5 and day not in set(holidays)


def add_business_days(start, n, holidays=()):
    """The business day ``n`` business days after ``start`` (``n`` >= 0; with
    ``n`` == 0, ``start`` itself when it is a business day, else the next one)."""
    holidays = set(holidays)
    day = start
    while not is_business_day(day, holidays):
        day += timedelta(days=1)
    for _ in range(n):
        day += timedelta(days=1)
        while not is_business_day(day, holidays):
            day += timedelta(days=1)
    return day


def business_days_between(start, end, holidays=()):
    """How many business days lie in [start, end)."""
    holidays = set(holidays)
    count = 0
    day = start
    while day < end:
        if is_business_day(day, holidays):
            count += 1
        day += timedelta(days=1)
    return count

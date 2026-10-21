"""Calendar helpers (Gregorian calendar)."""


def is_leap(year):
    """True when ``year`` has a 29 February."""
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def days_in_month(year, month):
    if not 1 <= month <= 12:
        raise ValueError("month must be 1-12")
    if month == 2:
        return 29 if is_leap(year) else 28
    if month in (4, 6, 9, 11):
        return 30
    return 31


def days_in_year(year):
    return sum(days_in_month(year, month) for month in range(1, 13))

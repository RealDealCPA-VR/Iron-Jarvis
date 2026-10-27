"""The password rules for back-office accounts."""

MIN_LENGTH = 12
_DIGITS = frozenset("0123456789")


def _folded(text):
    return text.casefold()


def password_problems(password, username):
    """Every rule ``password`` breaks, as a list of short sentences (empty
    when it is acceptable), in this order:

    * "too short" — fewer than MIN_LENGTH characters;
    * "no digit" — no character 0-9;
    * "no letter" — no letter at all;
    * "contains the user name" — the user name appears anywhere in it, in any
      mix of upper and lower case ("Ada" is in "my-ADA-2026!"); a user name
      shorter than 3 characters is never looked for.
    """
    checks = (
        ("too short", len(password) < MIN_LENGTH),
        ("no digit", not _DIGITS.intersection(password)),
        ("no letter", not any(ch.isalpha() for ch in password)),
        ("contains the user name", len(username) >= 3 and _folded(password).find(_folded(username)) >= 0),
    )
    return [problem for problem, broken in checks if broken]


def is_acceptable(password, username):
    return not password_problems(password, username)

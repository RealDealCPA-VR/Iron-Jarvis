"""The password rules for back-office accounts."""

MIN_LENGTH = 12


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
    problems = []
    if len(password) < MIN_LENGTH:
        problems.append("too short")
    if not any(ch in "0123456789" for ch in password):
        problems.append("no digit")
    if not any(ch.isalpha() for ch in password):
        problems.append("no letter")
    if len(username) >= 3 and username in password:
        problems.append("contains the user name")
    return problems


def is_acceptable(password, username):
    return not password_problems(password, username)

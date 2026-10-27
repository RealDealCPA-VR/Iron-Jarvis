import unittest

from accounts.passwords import is_acceptable, password_problems
from accounts.usernames import is_valid_username, normalize_username


class PasswordTest(unittest.TestCase):
    def test_a_good_password(self):
        self.assertEqual(password_problems("correct-horse-7", "ada"), [])
        self.assertTrue(is_acceptable("correct-horse-7", "ada"))

    def test_too_short_and_no_digit(self):
        self.assertEqual(password_problems("short", "ada"), ["too short", "no digit"])

    def test_the_user_name_in_capitals(self):
        self.assertEqual(password_problems("my-ADA-2026!", "ada"), ["contains the user name"])

    def test_the_user_name_capitalized(self):
        self.assertFalse(is_acceptable("Ada-is-great-9", "ada"))

    def test_a_short_user_name_is_not_looked_for(self):
        self.assertEqual(password_problems("al-is-here-2026", "al"), [])


class UsernameTest(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_username("  Ada.Byron "), "ada.byron")

    def test_valid(self):
        self.assertTrue(is_valid_username("ada_b"))
        self.assertFalse(is_valid_username("7ada"))
        self.assertFalse(is_valid_username("ab"))


if __name__ == "__main__":
    unittest.main()

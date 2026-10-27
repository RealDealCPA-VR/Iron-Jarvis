import unittest

from syncer import Remote, TransientError


class FlakyTransport:
    def __init__(self, failures):
        self.failures = failures
        self.calls = 0
        self.stored = {}

    def _maybe_fail(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise TransientError("dropped")

    def get(self, path):
        self._maybe_fail()
        return self.stored[path]

    def put(self, path, data):
        self._maybe_fail()
        self.stored[path] = data


class RemoteTest(unittest.TestCase):
    def test_put_and_get(self):
        remote = Remote("mem://x", FlakyTransport(0))
        remote.put("a", b"1")
        self.assertEqual(remote.get("a"), b"1")

    def test_the_default_setting(self):
        self.assertEqual(Remote("mem://x", FlakyTransport(0)).retry_limit, 3)

    def test_tries_again(self):
        transport = FlakyTransport(2)
        remote = Remote("mem://x", transport, retry_limit=2)
        remote.put("a", b"1")
        self.assertEqual(transport.calls, 3)

    def test_gives_up(self):
        remote = Remote("mem://x", FlakyTransport(9), retry_limit=1)
        with self.assertRaises(TransientError):
            remote.put("a", b"1")


if __name__ == "__main__":
    unittest.main()

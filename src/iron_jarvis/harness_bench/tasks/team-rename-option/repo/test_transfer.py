import unittest

from syncer import Remote, TransientError, download, upload
from test_remote import FlakyTransport


class TransferTest(unittest.TestCase):
    def test_upload_in_name_order(self):
        transport = FlakyTransport(0)
        remote = Remote("mem://x", transport)
        self.assertEqual(upload(remote, {"b": b"2", "a": b"1"}), ["a", "b"])
        self.assertEqual(transport.stored, {"a": b"1", "b": b"2"})

    def test_download(self):
        transport = FlakyTransport(0)
        transport.stored = {"a": b"1"}
        self.assertEqual(download(Remote("mem://x", transport), ["a"]), {"a": b"1"})

    def test_own_setting_wins_over_the_remotes(self):
        remote = Remote("mem://x", FlakyTransport(2), max_retries=5)
        with self.assertRaises(TransientError):
            upload(remote, {"a": b"1"}, max_retries=1)

    def test_the_remotes_setting_by_default(self):
        transport = FlakyTransport(2)
        remote = Remote("mem://x", transport, max_retries=2)
        self.assertEqual(upload(remote, {"a": b"1"}), ["a"])


if __name__ == "__main__":
    unittest.main()

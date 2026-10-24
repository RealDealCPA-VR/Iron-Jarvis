"""The server's own tests (they do not open a browser)."""

import json
import threading
import unittest
import urllib.request

import server


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = server.create_server(port=0)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as response:
            return response.status, response.read().decode("utf-8")

    def test_the_api_lists_every_item(self):
        status, body = self.get("/api/items")
        self.assertEqual(status, 200)
        items = json.loads(body)
        self.assertEqual(len(items), 24)
        self.assertEqual(set(items[0]), {"sku", "name", "qty", "reorder_at"})

    def test_the_page_and_its_script_are_served(self):
        status, page = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn('id="items"', page)
        self.assertIn("app.js", page)
        status, script = self.get("/app.js")
        self.assertEqual(status, 200)
        self.assertIn("/api/items", script)


if __name__ == "__main__":
    unittest.main()

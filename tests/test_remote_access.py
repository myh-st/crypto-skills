"""Private remote access: the server stays loopback-only; only an operator-listed HTTPS proxy origin
(e.g. a tailnet-only `tailscale serve`) is accepted besides same-origin loopback requests."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from crypto_eval.paper_runtime import PaperRuntime, PaperScheduler, PaperStore
from crypto_eval.paper_server import PaperHTTPServer, PaperRequestHandler, serve, trusted_origins

PROXY = "https://my-mac.tail1234.ts.net"


class RemoteAccessTests(unittest.TestCase):
    def setUp(self):
        store = PaperStore(Path(tempfile.mkdtemp()) / "r.sqlite3")
        self.addCleanup(store.close)
        runtime = PaperRuntime(store)
        scheduler = PaperScheduler(runtime)
        server = PaperHTTPServer(("127.0.0.1", 0), PaperRequestHandler, runtime, scheduler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(scheduler.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(worker.join, 5)
        self.addCleanup(server.shutdown)
        self.base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(self, headers):
        request = urllib.request.Request(f"{self.base}/api/experiment/validate", data=b"{}", method="POST",
                                         headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def rejected(self, headers):
        status, body = self.call(headers)
        return status >= 400 and "cross-" in str(body.get("error", ""))

    def test_proxy_origin_needs_an_exact_opt_in(self):
        proxy = {"Host": "my-mac.tail1234.ts.net", "Origin": PROXY}
        with mock.patch.dict(os.environ, {"PAPER_TRUSTED_ORIGINS": ""}):
            self.assertTrue(self.rejected(proxy))
        with mock.patch.dict(os.environ, {"PAPER_TRUSTED_ORIGINS": PROXY}):
            self.assertFalse(self.rejected(proxy))
            # a different site claiming the proxy's Host is still refused
            self.assertTrue(self.rejected({"Host": "my-mac.tail1234.ts.net", "Origin": "https://evil.example"}))
            # the listed origin must match the Host it was forwarded with
            self.assertTrue(self.rejected({"Host": "other.tail1234.ts.net", "Origin": PROXY}))
            self.assertTrue(self.rejected({**proxy, "Sec-Fetch-Site": "cross-site"}))
        # same-origin loopback keeps working with or without the setting
        host = self.base.split("//")[1]
        self.assertFalse(self.rejected({"Host": host, "Origin": self.base}))

    def test_only_exact_https_origins_are_trusted(self):
        with mock.patch.dict(os.environ, {"PAPER_TRUSTED_ORIGINS": f"http://plain.example, https://*.ts.net, {PROXY}/"}):
            self.assertEqual(trusted_origins(), frozenset({PROXY}))

    def test_the_server_still_refuses_to_bind_beyond_loopback(self):
        with self.assertRaises(Exception):
            serve(host="0.0.0.0", port=0, database=Path(tempfile.mkdtemp()) / "x.sqlite3", live_stream=False)


if __name__ == "__main__":
    unittest.main()

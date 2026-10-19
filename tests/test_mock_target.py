"""
Tests for the offline mock target used by the CI smoke test (`tools/mock_target.py`).
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from typing import Any, Optional, Tuple

import locustfile
from tools.mock_target import MOCK_SERVICE_NAME, create_mock_target


class TestMockTarget(unittest.TestCase):
    """Verify the mock target answers every persona route the swarm probes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = create_mock_target(host="127.0.0.1", port=0)
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> Tuple[int, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_every_persona_route_answers_200(self) -> None:
        expected = [
            (endpoint["method"], endpoint["path"], spec["domain"])
            for spec in locustfile.PERSONA_SPECS.values()
            for endpoint in spec["endpoints"]
        ]
        self.assertEqual(len(expected), 8)
        for method, path, domain in expected:
            status, payload = self._request(method, path, {"ping": 1} if method == "POST" else None)
            self.assertEqual(status, 200, f"{method} {path}")
            self.assertTrue(payload["ok"], f"{method} {path}")
            self.assertEqual(payload["mock"], MOCK_SERVICE_NAME)
            self.assertEqual(payload["method"], method)
            self.assertEqual(payload["path"], path)
            self.assertEqual(payload["domain"], domain)

    def test_health_endpoint(self) -> None:
        status, payload = self._request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["service"], MOCK_SERVICE_NAME)

    def test_unknown_route_returns_404(self) -> None:
        status, payload = self._request("GET", "/nope")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"], "not_found")

    def test_post_body_is_echoed(self) -> None:
        status, payload = self._request("POST", "/quote-parse/", {"rfq_id": "RFQ-1"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["echo"], {"rfq_id": "RFQ-1"})


if __name__ == "__main__":
    unittest.main()

"""live_probe may only reach allowlisted hosts, never follows redirects, and
bad query values do not drop the connection."""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Tuple

from swarm_service.controller import SwarmController
from swarm_service.server import DEFAULT_ALLOWED_TARGET_HOSTS, create_server, parse_allowed_target_hosts


def _serve(handler: type) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class _Counting(BaseHTTPRequestHandler):
    hits: List[str] = []

    def log_message(self, *args: Any) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        type(self).hits.append(self.path)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"secret")

    do_POST = do_GET  # noqa: N815


class LiveProbePolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _Counting.hits = []
        cls.internal = _serve(_Counting)
        cls.server, _ = create_server(host="127.0.0.1", port=0)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        for srv in (cls.server, cls.internal):
            srv.shutdown()
            srv.server_close()

    def _post(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        req = urllib.request.Request(
            f"{self.base}/api/v1/swarm/simulate",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_live_probe_refuses_targets_outside_the_allowlist(self) -> None:
        internal = f"http://127.0.0.1:{self.internal.server_address[1]}"
        for target in (internal, "http://169.254.169.254", "http://localhost:8090", "https://evil.example",
                       "file:///etc/passwd", "gopher://api.globlex.pro", "not a url"):
            status, body = self._post({"mode": "live_probe", "target_host": target, "vus": 1, "duration_sec": 1})
            self.assertEqual(status, 403, target)
            self.assertEqual(body["error"], "forbidden_target")
        self.assertEqual(_Counting.hits, [], "no request may reach a refused target")

    def test_deterministic_mode_never_touches_the_network(self) -> None:
        internal = f"http://127.0.0.1:{self.internal.server_address[1]}"
        status, _ = self._post({"mode": "deterministic", "target_host": internal, "vus": 1, "duration_sec": 1})
        self.assertEqual(status, 200)
        self.assertEqual(_Counting.hits, [])

    def test_invalid_runs_limit_falls_back_instead_of_dropping_the_connection(self) -> None:
        with urllib.request.urlopen(f"{self.base}/api/v1/swarm/runs?limit=abc", timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("runs", json.loads(resp.read()))

    def test_allowlist_parsing(self) -> None:
        self.assertEqual(parse_allowed_target_hosts(""), DEFAULT_ALLOWED_TARGET_HOSTS)
        self.assertEqual(parse_allowed_target_hosts(" Staging.Example.com, ,api.test "),
                         ("staging.example.com", "api.test"))


class RedirectTest(unittest.TestCase):
    def test_probe_does_not_follow_redirects(self) -> None:
        _Counting.hits = []
        landing = _serve(_Counting)
        landing_url = f"http://127.0.0.1:{landing.server_address[1]}/landed"

        class _Redirect(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                self.send_response(302)
                self.send_header("Location", landing_url)
                self.end_headers()

        bouncer = _serve(_Redirect)
        try:
            result = SwarmController()._probe_live_endpoint(
                target_host=f"http://127.0.0.1:{bouncer.server_address[1]}", method="GET", path="/start"
            )
            self.assertEqual(result["status_code"], 302)
            self.assertEqual(_Counting.hits, [], "the redirect target must not be contacted")
        finally:
            for srv in (bouncer, landing):
                srv.shutdown()
                srv.server_close()


if __name__ == "__main__":
    unittest.main()

"""
Unit and HTTP Integration Tests for PerfOps Locust B2B User Swarm Service.
"""

from __future__ import annotations

import io
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from typing import Any

import locustfile
from swarm_service.controller import SwarmController, compute_apdex, compute_percentiles
from swarm_service.server import create_server


class TestLocustfilePersonas(unittest.TestCase):
    """Verify B2B persona definitions and standalone task execution in locustfile.py."""

    def test_persona_weights_and_catalog(self) -> None:
        catalog = locustfile.get_personas_catalog()
        self.assertEqual(len(catalog), 3)
        by_name = {item["persona"]: item for item in catalog}

        self.assertEqual(by_name["GloblexShopBuyerUser"]["weight"], 50)
        self.assertEqual(by_name["GloblexShopBuyerUser"]["domain"], "globlex.shop")
        shop_paths = [ep["path"] for ep in by_name["GloblexShopBuyerUser"]["endpoints"]]
        self.assertEqual(shop_paths, ["/companies/", "/quote-parse/", "/enterprise/overview"])

        self.assertEqual(by_name["GloblexProContractorUser"]["weight"], 30)
        self.assertEqual(by_name["GloblexProContractorUser"]["domain"], "globlex.pro")
        pro_paths = [ep["path"] for ep in by_name["GloblexProContractorUser"]["endpoints"]]
        self.assertEqual(
            pro_paths,
            ["/enterprise/tenders", "/inn-check/6671094821", "/enterprise/master-os/overview"],
        )

        self.assertEqual(by_name["GloblexOnlineAiCopilotUser"]["weight"], 20)
        self.assertEqual(by_name["GloblexOnlineAiCopilotUser"]["domain"], "globlex.online")
        ai_paths = [ep["path"] for ep in by_name["GloblexOnlineAiCopilotUser"]["endpoints"]]
        self.assertEqual(ai_paths, ["/ai-chat/", "/health"])

    def test_user_task_methods_record_requests(self) -> None:
        buyer = locustfile.GloblexShopBuyerUser(host="https://api.globlex.pro")
        buyer.browse_companies()
        buyer.parse_quote()
        buyer.enterprise_overview()
        self.assertEqual(len(buyer.client.history), 3)
        self.assertEqual(buyer.client.history[0]["path"], "/companies/")
        self.assertEqual(buyer.client.history[1]["path"], "/quote-parse/")
        self.assertEqual(buyer.client.history[1]["method"], "POST")
        self.assertEqual(buyer.client.history[2]["path"], "/enterprise/overview")

        contractor = locustfile.GloblexProContractorUser(host="https://api.globlex.pro")
        contractor.inspect_tenders()
        contractor.verify_counterparty_inn()
        contractor.master_os_overview()
        self.assertEqual(len(contractor.client.history), 3)
        self.assertEqual(contractor.client.history[1]["path"], "/inn-check/6671094821")

        copilot = locustfile.GloblexOnlineAiCopilotUser(host="https://api.globlex.pro")
        copilot.send_ai_chat_prompt()
        copilot.check_health()
        self.assertEqual(len(copilot.client.history), 2)
        self.assertEqual(copilot.client.history[0]["path"], "/ai-chat/")
        self.assertEqual(copilot.client.history[1]["path"], "/health")


class TestSwarmControllerMath(unittest.TestCase):
    """Verify percentile math, Apdex calculation, deterministic simulation, and live probe."""

    def test_percentiles_and_apdex(self) -> None:
        samples = [50.0, 100.0, 150.0, 200.0, 250.0, 300.0, 400.0, 900.0]
        pcts = compute_percentiles(samples)
        self.assertEqual(pcts["min_ms"], 50.0)
        self.assertEqual(pcts["max_ms"], 900.0)
        self.assertGreaterEqual(pcts["p95_ms"], pcts["p50_ms"])
        self.assertGreaterEqual(pcts["p99_ms"], pcts["p95_ms"])

        # For T=200ms: <=200 satisfied (4), 200..800 tolerating (3), >800 frustrated (1)
        # Apdex = (4 + 0.5*3) / 8 = 5.5 / 8 = 0.6875
        apdex = compute_apdex(samples, target_t_ms=200.0)
        self.assertAlmostEqual(apdex, 0.6875, places=4)

    def test_deterministic_simulation_reproducibility(self) -> None:
        ctrl = SwarmController()
        run1 = ctrl.simulate(vus=60, duration_sec=20, target_host="https://api.globlex.pro", seed=99)
        run2 = ctrl.simulate(vus=60, duration_sec=20, target_host="https://api.globlex.pro", seed=99)

        self.assertEqual(run1["summary"], run2["summary"])
        self.assertEqual(len(run1["endpoints"]), 8)
        self.assertIn("launchgate", run1)
        self.assertEqual(run1["launchgate"]["schema_version"], "launchgate-core/v1")
        self.assertTrue(run1["launchgate"]["ready"])
        self.assertEqual(run1["launchgate"]["verdict"], "PASS")
        self.assertEqual(len(ctrl.get_runs()), 2)

    def test_live_probe_mode_with_mock_opener(self) -> None:
        class FakeHTTPResponse:
            status = 200

            def read(self, _n: int = -1) -> bytes:
                return b'{"ok":true}'

            def __enter__(self) -> "FakeHTTPResponse":
                return self

            def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
                return None

        probed_urls = []

        def fake_opener(req: urllib.request.Request, timeout: float = 2.5) -> FakeHTTPResponse:
            probed_urls.append(req.full_url)
            return FakeHTTPResponse()

        ctrl = SwarmController()
        res = ctrl.simulate(
            vus=30,
            duration_sec=15,
            target_host="https://api.globlex.pro",
            mode="live_probe",
            http_opener=fake_opener,
        )
        self.assertEqual(len(probed_urls), 8)
        for ep in res["endpoints"]:
            self.assertIn("live_probe", ep)
            self.assertTrue(ep["live_probe"]["ok"])


class TestSwarmHTTPServer(unittest.TestCase):
    """Integration tests for SwarmRequestHandler HTTP endpoints."""

    @classmethod
    def setUpClass(cls) -> None:
        os.environ["SWARM_QUIET_LOGS"] = "1"
        cls.server, cls.controller = create_server(host="127.0.0.1", port=0)
        cls.port = cls.server.server_address[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def _request_json(self, method: str, path: str, body: Any = None) -> tuple[int, dict[str, Any]]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode("utf-8"))
            finally:
                exc.close()

    def test_health_endpoint(self) -> None:
        status, payload = self._request_json("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(
            payload,
            {"status": "ok", "service": "perfops-locust-swarm", "version": "1.0.0"},
        )

    def test_personas_endpoint(self) -> None:
        status, payload = self._request_json("GET", "/api/v1/personas")
        self.assertEqual(status, 200)
        self.assertEqual(payload["total_weight"], 100)
        names = [p["persona"] for p in payload["personas"]]
        self.assertEqual(
            names,
            [
                "GloblexShopBuyerUser",
                "GloblexProContractorUser",
                "GloblexOnlineAiCopilotUser",
            ],
        )

    def test_simulate_and_runs_endpoints(self) -> None:
        status, sim = self._request_json(
            "POST",
            "/api/v1/swarm/simulate",
            {
                "vus": 80,
                "duration_sec": 25,
                "target_host": "https://api.globlex.pro",
                "seed": 7,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(sim["config"]["vus"], 80)
        self.assertGreater(sim["summary"]["total_rps"], 0)
        self.assertIn("launchgate", sim)
        self.assertIn("GloblexShopBuyerUser", sim["personas"])

        runs_status, runs_payload = self._request_json("GET", "/api/v1/swarm/runs")
        self.assertEqual(runs_status, 200)
        self.assertGreaterEqual(runs_payload["count"], 1)
        self.assertEqual(runs_payload["runs"][0]["run_id"], sim["run_id"])

    def test_invalid_simulation_request_returns_400(self) -> None:
        status, err = self._request_json("POST", "/api/v1/swarm/simulate", {"vus": -5})
        self.assertEqual(status, 400)
        self.assertEqual(err["error"], "bad_request")


if __name__ == "__main__":
    unittest.main()

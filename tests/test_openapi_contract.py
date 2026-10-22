"""Keeps ROUTES in swarm_service/server.py and docs/api/openapi.json in lockstep."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from swarm_service.server import ROUTES

SPEC = json.loads((Path(__file__).resolve().parents[1] / "docs" / "api" / "openapi.json").read_text())
METHODS = {"get", "put", "post", "delete", "patch", "head", "options"}


def spec_operations():
    for path, item in SPEC["paths"].items():
        for method, operation in item.items():
            if method in METHODS:
                yield f"{method.upper()} {path}", operation


class OpenApiContractTest(unittest.TestCase):
    def test_spec_is_openapi_3(self) -> None:
        self.assertRegex(SPEC["openapi"], r"^3\.\d+\.\d+$")

    def test_served_and_documented_routes_match(self) -> None:
        served = sorted(f"{route.method} {route.path}" for route in ROUTES)
        documented = sorted(key for key, _ in spec_operations())
        self.assertEqual(documented, served)

    def test_operations_are_complete(self) -> None:
        seen = set()
        for key, operation in spec_operations():
            self.assertTrue(operation.get("operationId"), f"{key} lacks operationId")
            self.assertNotIn(operation["operationId"], seen, "duplicate operationId")
            seen.add(operation["operationId"])
            self.assertTrue(operation.get("summary"), f"{key} lacks summary")
            self.assertTrue(any(re.match(r"2\d\d", code) for code in operation["responses"]), f"{key} lacks a 2xx")
            media = operation.get("requestBody", {}).get("content", {}).get("application/json")
            if media:
                self.assertTrue("example" in media or "examples" in media, f"{key} body lacks an example")


if __name__ == "__main__":
    unittest.main()

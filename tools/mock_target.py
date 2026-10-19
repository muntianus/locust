#!/usr/bin/env python3
"""
Zero-dependency mock target for offline swarm smoke tests.

Serves every route declared in ``locustfile.PERSONA_SPECS`` with HTTP 200 JSON, so
``POST /api/v1/swarm/simulate`` can run in ``live_probe`` mode against
``http://127.0.0.1:<port>`` instead of the public ``api.globlex.pro`` (which is not
always reachable from CI and answers 422/404 on some persona routes).

Usage:
    python3 tools/mock_target.py --port 18080              # foreground
    python3 tools/mock_target.py --port 0 --print-port     # ephemeral port
    python3 tools/mock_target.py --delay-ms 250            # emulate a slow target
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from locustfile import PERSONA_SPECS  # noqa: E402

MOCK_SERVICE_NAME = "perfops-locust-mock-target"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18080


def build_routes(persona_specs: Dict[str, Dict[str, Any]] = PERSONA_SPECS) -> Dict[Tuple[str, str], str]:
    """Map ``(METHOD, path)`` -> persona domain for every route the swarm probes."""
    routes: Dict[Tuple[str, str], str] = {}
    for spec in persona_specs.values():
        for endpoint in spec["endpoints"]:
            routes[(str(endpoint["method"]).upper(), str(endpoint["path"]))] = str(spec["domain"])
    return routes


class MockTargetHandler(BaseHTTPRequestHandler):
    """Answers every persona route with a deterministic 200 JSON payload."""

    routes: Dict[Tuple[str, str], str] = build_routes()
    delay_ms: float = 0.0
    quiet: bool = False

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        if self.quiet:
            return
        super().log_message(format, *args)

    def _send_json(self, status_code: int, payload: Dict[str, Any]) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_body(self) -> Optional[Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return None
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    def _serve(self, method: str) -> None:
        path = self.path.split("?", 1)[0]
        body = self._read_body()
        if self.delay_ms > 0:
            time.sleep(self.delay_ms / 1000.0)

        if path in ("/health", "/api/health"):
            health: Dict[str, Any] = {
                "ok": True,
                "status": "ok",
                "service": MOCK_SERVICE_NAME,
                "mock": MOCK_SERVICE_NAME,
                "method": method,
                "path": path,
            }
            # `GET /health` is also a swarm persona route (GloblexOnlineAiCopilotUser).
            if (method, path) in self.routes:
                health["domain"] = self.routes[(method, path)]
            self._send_json(200, health)
            return

        domain = self.routes.get((method, path))
        if domain is None:
            self._send_json(
                404,
                {"ok": False, "error": "not_found", "method": method, "path": path},
            )
            return

        payload: Dict[str, Any] = {
            "ok": True,
            "mock": MOCK_SERVICE_NAME,
            "method": method,
            "path": path,
            "domain": domain,
        }
        if body is not None:
            payload["echo"] = body
        self._send_json(200, payload)

    def do_GET(self) -> None:  # noqa: N802
        self._serve("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._serve("POST")


def create_mock_target(
    host: str = DEFAULT_HOST,
    port: int = 0,
    delay_ms: float = 0.0,
    quiet: bool = True,
) -> ThreadingHTTPServer:
    """Create a bound mock target server (``port=0`` picks a free ephemeral port)."""

    class BoundHandler(MockTargetHandler):
        pass

    BoundHandler.routes = build_routes()
    BoundHandler.delay_ms = float(delay_ms)
    BoundHandler.quiet = quiet
    return ThreadingHTTPServer((host, int(port)), BoundHandler)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=f"{MOCK_SERVICE_NAME}: offline smoke target")
    parser.add_argument("--host", default=os.environ.get("MOCK_TARGET_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MOCK_TARGET_PORT", DEFAULT_PORT)))
    parser.add_argument(
        "--delay-ms",
        type=float,
        default=float(os.environ.get("MOCK_DELAY_MS", "0")),
        help="artificial response latency",
    )
    parser.add_argument("--print-port", action="store_true", help="print the bound port and flush")
    parser.add_argument(
        "--quiet",
        action="store_true",
        default=os.environ.get("MOCK_TARGET_QUIET") == "1",
        help="suppress per-request access logs",
    )
    args = parser.parse_args(argv)

    server = create_mock_target(
        host=args.host,
        port=args.port,
        delay_ms=args.delay_ms,
        quiet=args.quiet,
    )
    bound_port = int(server.server_address[1])
    if args.print_port:
        print(bound_port, flush=True)
    else:
        print(f"[{MOCK_SERVICE_NAME}] listening on http://{args.host}:{bound_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Standalone Zero-Dependency HTTP Swarm Controller API Server (`PORT` default 4070).

Endpoints:
  - GET  /health
  - GET  /api/v1/personas
  - POST /api/v1/swarm/simulate
  - GET  /api/v1/swarm/runs
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from .controller import DEFAULT_APDEX_T_MS, DEFAULT_SLO_P95_MS, DEFAULT_TARGET_HOST, SwarmController

SERVICE_NAME = "perfops-locust-swarm"
SERVICE_VERSION = "1.0.0"
DEFAULT_PORT = 4070


class SwarmRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for PerfOps Locust B2B Swarm Controller."""

    controller: SwarmController = SwarmController()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        if os.environ.get("SWARM_QUIET_LOGS") == "1":
            return
        super().log_message(format, *args)

    def _send_json(self, status_code: int, payload: Dict[str, Any]) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        if not raw.strip():
            return {}
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("JSON request body must be an object")
        return data

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path in ("/health", "/api/health"):
            self._send_json(
                200,
                {
                    "status": "ok",
                    "service": SERVICE_NAME,
                    "version": SERVICE_VERSION,
                },
            )
            return

        if path == "/api/v1/personas":
            self._send_json(200, self.controller.get_personas())
            return

        if path == "/api/v1/swarm/runs":
            query = parse_qs(parsed.query)
            limit = int(query.get("limit", ["25"])[0])
            runs = self.controller.get_runs(limit=limit)
            self._send_json(
                200,
                {
                    "service": SERVICE_NAME,
                    "count": len(runs),
                    "runs": runs,
                },
            )
            return

        self._send_json(
            404,
            {
                "error": "not_found",
                "message": f"Route {path} not found",
            },
        )

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/api/v1/swarm/simulate":
            try:
                body = self._read_json_body()
                result = self.controller.simulate(
                    vus=int(body.get("vus", 50)),
                    duration_sec=float(body.get("duration_sec", 30.0)),
                    target_host=str(body.get("target_host", DEFAULT_TARGET_HOST)),
                    persona_weights=body.get("persona_weights"),
                    mode=str(body.get("mode", "deterministic")),
                    seed=body.get("seed", 42),
                    apdex_t_ms=float(body.get("apdex_t_ms", DEFAULT_APDEX_T_MS)),
                    slo_p95_ms=float(body.get("slo_p95_ms", DEFAULT_SLO_P95_MS)),
                )
                self._send_json(200, result)
            except (ValueError, json.JSONDecodeError) as exc:
                self._send_json(
                    400,
                    {
                        "error": "bad_request",
                        "message": str(exc),
                    },
                )
            return

        self._send_json(
            404,
            {
                "error": "not_found",
                "message": f"Route {path} not found",
            },
        )


def create_server(
    host: str = "0.0.0.0",
    port: int = DEFAULT_PORT,
    controller: Optional[SwarmController] = None,
) -> Tuple[ThreadingHTTPServer, SwarmController]:
    """Create a ThreadingHTTPServer bound to (host, port) with an isolated SwarmController."""
    ctrl = controller or SwarmController()

    class BoundHandler(SwarmRequestHandler):
        controller = ctrl

    server = ThreadingHTTPServer((host, int(port)), BoundHandler)
    return server, ctrl


def run_server(host: Optional[str] = None, port: Optional[int] = None) -> None:
    bind_host = host or os.environ.get("HOST", "0.0.0.0")
    bind_port = int(port if port is not None else os.environ.get("PORT", str(DEFAULT_PORT)))
    server, _ = create_server(host=bind_host, port=bind_port)
    print(f"[{SERVICE_NAME}] listening on http://{bind_host}:{bind_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run_server()

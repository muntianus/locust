#!/usr/bin/env python3
"""
Self-contained CI smoke test for the PerfOps Locust swarm service.

Default (offline) mode boots two loopback-only servers, so the smoke never touches
the public internet:

  1. ``tools/mock_target.py``      - answers every persona route with HTTP 200
  2. ``python3 -m swarm_service.server`` on an ephemeral port

The swarm then runs in both ``deterministic`` and ``live_probe`` modes against
``http://127.0.0.1:<mock_port>``. Set ``TARGET_HOST`` (or the k6-style alias
``__ENV.TARGET_HOST``) to smoke a real environment instead; the external domain is
never used by default.

Thresholds are deliberately slack: this is a liveness/contract check on arbitrary
CI hardware, not a latency benchmark. Startup is polled instead of ``sleep``-ed.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, TextIO, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SERVICE_NAME = "perfops-locust-swarm"

# Smoke profile: intentionally light, so a slow shared runner has plenty of headroom.
SMOKE_VUS = int(os.environ.get("SMOKE_VUS", "10"))
SMOKE_DURATION_SEC = float(os.environ.get("SMOKE_DURATION_SEC", "5"))
SMOKE_SLO_P95_MS = float(os.environ.get("SMOKE_SLO_P95_MS", "5000"))
SMOKE_APDEX_T_MS = float(os.environ.get("SMOKE_APDEX_T_MS", "5000"))

READY_TIMEOUT_SEC = float(os.environ.get("SMOKE_READY_TIMEOUT_SEC", "60"))
READY_INTERVAL_SEC = 0.2
REQUEST_TIMEOUT_SEC = float(os.environ.get("SMOKE_REQUEST_TIMEOUT_SEC", "15"))
EXPECTED_ENDPOINTS = 8
PERSONAS = ("GloblexShopBuyerUser", "GloblexProContractorUser", "GloblexOnlineAiCopilotUser")


class SmokeFailure(RuntimeError):
    """Raised when a smoke assertion fails."""


def _free_port(exclude: Optional[set] = None) -> int:
    """Ask the OS for a free loopback port, never returning one already claimed."""
    port = 0
    for _ in range(20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        if not exclude or port not in exclude:
            return port
    return port


def _request_json(
    method: str,
    url: str,
    body: Optional[Dict[str, Any]] = None,
    timeout: float = REQUEST_TIMEOUT_SEC,
) -> Tuple[int, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return int(resp.status), json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as exc:  # keep the payload for assertions
        with exc:
            raw = exc.read().decode("utf-8")
        try:
            return int(exc.code), json.loads(raw) if raw.strip() else None
        except json.JSONDecodeError:
            return int(exc.code), raw
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SmokeFailure(f"{method} {url} failed: {exc}") from exc


def _wait_ready(url: str, proc: subprocess.Popen, log: TextIO, what: str) -> Dict[str, Any]:
    """Poll until the service answers or the deadline expires (replaces `sleep 1`)."""
    deadline = time.monotonic() + READY_TIMEOUT_SEC
    last_error = "no attempt made"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            log.seek(0)
            raise SmokeFailure(
                f"{what} exited with code {proc.returncode} before becoming ready:\n{log.read()[-4000:]}"
            )
        try:
            status, payload = _request_json("GET", url, timeout=2.0)
            if status == 200 and isinstance(payload, dict):
                return payload
            last_error = f"HTTP {status}: {payload!r}"
        except SmokeFailure as exc:  # connection refused while booting
            last_error = str(exc)
        time.sleep(READY_INTERVAL_SEC)

    log.seek(0)
    raise SmokeFailure(
        f"{what} not ready after {READY_TIMEOUT_SEC:.0f}s ({last_error}):\n{log.read()[-4000:]}"
    )


def _spawn(cmd: List[str], env: Dict[str, str], log: TextIO) -> subprocess.Popen:
    return subprocess.Popen(
        cmd,
        cwd=REPO_ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )


def _stop(proc: Optional[subprocess.Popen]) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _simulate(base_url: str, target_host: str, **overrides: Any) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "vus": SMOKE_VUS,
        "duration_sec": SMOKE_DURATION_SEC,
        "target_host": target_host,
        "seed": 42,
        "apdex_t_ms": SMOKE_APDEX_T_MS,
        "slo_p95_ms": SMOKE_SLO_P95_MS,
    }
    body.update(overrides)
    status, payload = _request_json("POST", f"{base_url}/api/v1/swarm/simulate", body)
    if status != 200 or not isinstance(payload, dict):
        raise SmokeFailure(f"simulate ({body.get('mode', 'deterministic')}) -> HTTP {status}: {payload!r}")
    return payload


def _assert_deterministic(report: Dict[str, Any]) -> None:
    launchgate = report.get("launchgate") or {}
    if launchgate.get("schema_version") != "launchgate-core/v1":
        raise SmokeFailure(f"deterministic: launchgate schema missing ({launchgate!r})")
    if not launchgate.get("ready") or launchgate.get("verdict") != "PASS":
        raise SmokeFailure(f"deterministic: launchgate not PASS ({launchgate!r})")
    if (report.get("summary") or {}).get("total_rps", 0) <= 0:
        raise SmokeFailure(f"deterministic: no throughput ({report.get('summary')!r})")
    if len(report.get("endpoints") or []) != EXPECTED_ENDPOINTS:
        raise SmokeFailure(f"deterministic: expected {EXPECTED_ENDPOINTS} endpoints, got {len(report.get('endpoints') or [])}")
    missing = [name for name in PERSONAS if name not in (report.get("personas") or {})]
    if missing:
        raise SmokeFailure(f"deterministic: missing personas {missing}")


def _assert_live_probe(report: Dict[str, Any]) -> None:
    endpoints = report.get("endpoints") or []
    if len(endpoints) != EXPECTED_ENDPOINTS:
        raise SmokeFailure(f"live_probe: expected {EXPECTED_ENDPOINTS} probes, got {len(endpoints)}")
    broken = [
        f"{ep.get('method')} {ep.get('path')} -> {ep.get('live_probe')!r}"
        for ep in endpoints
        if not (ep.get("live_probe") or {}).get("ok")
    ]
    if broken:
        raise SmokeFailure("live_probe: target probes failed:\n  " + "\n  ".join(broken))
    error_rate = (report.get("summary") or {}).get("error_rate")
    if error_rate != 0:
        raise SmokeFailure(f"live_probe: expected error_rate 0, got {error_rate!r}")
    launchgate = report.get("launchgate") or {}
    if launchgate.get("verdict") != "PASS":
        raise SmokeFailure(f"live_probe: launchgate not PASS ({launchgate!r})")


def run() -> None:
    external_target = (os.environ.get("TARGET_HOST") or os.environ.get("__ENV.TARGET_HOST") or "").strip()
    mode = "external" if external_target else "offline"
    print(f"==> smoke mode: {mode} (vus={SMOKE_VUS}, duration={SMOKE_DURATION_SEC}s, "
          f"slo_p95={SMOKE_SLO_P95_MS:.0f}ms, ready_timeout={READY_TIMEOUT_SEC:.0f}s)")

    env = {k: v for k, v in os.environ.items() if k not in ("TARGET_HOST", "__ENV.TARGET_HOST")}
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("SWARM_QUIET_LOGS", "1")
    env.setdefault("MOCK_TARGET_QUIET", "1")
    env["PYTHONPATH"] = REPO_ROOT + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    mock_proc: Optional[subprocess.Popen] = None
    swarm_proc: Optional[subprocess.Popen] = None
    mock_log = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
    swarm_log = tempfile.TemporaryFile(mode="w+", encoding="utf-8")

    try:
        target_host = external_target
        mock_port: Optional[int] = None
        if not target_host:
            mock_port = _free_port()
            mock_proc = _spawn(
                [sys.executable, "tools/mock_target.py", "--port", str(mock_port), "--quiet"],
                env,
                mock_log,
            )
            target_host = f"http://127.0.0.1:{mock_port}"
            _wait_ready(f"{target_host}/health", mock_proc, mock_log, "mock target")
            print(f"    mock target ready at {target_host}")

        swarm_port = _free_port({mock_port} if mock_port else None)
        swarm_proc = _spawn(
            [sys.executable, "-m", "swarm_service.server"],
            {**env, "PORT": str(swarm_port), "HOST": "127.0.0.1"},
            swarm_log,
        )
        base_url = f"http://127.0.0.1:{swarm_port}"
        health = _wait_ready(f"{base_url}/health", swarm_proc, swarm_log, "swarm service")
        if health.get("service") != SERVICE_NAME:
            raise SmokeFailure(f"/health: unexpected service {health.get('service')!r}")
        print(f"    swarm service ready at {base_url}")

        status, personas = _request_json("GET", f"{base_url}/api/v1/personas")
        if status != 200 or not isinstance(personas, dict):
            raise SmokeFailure(f"/api/v1/personas -> HTTP {status}: {personas!r}")
        found = [p.get("persona") for p in personas.get("personas") or []]
        if found != list(PERSONAS):
            raise SmokeFailure(f"/api/v1/personas: unexpected catalog {found!r}")
        if personas.get("total_weight") != 100:
            raise SmokeFailure(f"/api/v1/personas: unexpected total_weight {personas.get('total_weight')!r}")
        print(f"    personas catalogue ok: {', '.join(found)}")

        deterministic = _simulate(base_url, target_host, mode="deterministic")
        _assert_deterministic(deterministic)
        print(f"    deterministic: PASS rps={deterministic['summary']['total_rps']} "
              f"p95={deterministic['summary']['p95_ms']}ms")

        live = _simulate(base_url, target_host, mode="live_probe")
        _assert_live_probe(live)
        probed = float((live["summary"] or {}).get("error_rate", 0))
        print(f"    live_probe: {len(live['endpoints'])}/{EXPECTED_ENDPOINTS} probes ok "
              f"against {target_host}, error_rate={probed}")

        runs_status, runs = _request_json("GET", f"{base_url}/api/v1/swarm/runs?limit=5")
        if runs_status != 200 or not isinstance(runs, dict):
            raise SmokeFailure(f"/api/v1/swarm/runs -> HTTP {runs_status}: {runs!r}")
        run_ids = [run.get("run_id") for run in runs.get("runs") or []]
        if live.get("run_id") not in run_ids:
            raise SmokeFailure(f"/api/v1/swarm/runs: latest run {live.get('run_id')!r} missing from {run_ids!r}")
        print(f"    runs history ok: {len(run_ids)} run(s) retained")

        status, err = _request_json("POST", f"{base_url}/api/v1/swarm/simulate", {"vus": -5})
        if status != 400 or (err or {}).get("error") != "bad_request":
            raise SmokeFailure(f"invalid request: expected 400/bad_request, got HTTP {status}: {err!r}")
        print("    invalid request still rejected with 400")

    finally:
        _stop(swarm_proc)
        _stop(mock_proc)
        mock_log.close()
        swarm_log.close()

    print("==> smoke PASS")


def main() -> int:
    try:
        run()
    except SmokeFailure as exc:
        print(f"==> smoke FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

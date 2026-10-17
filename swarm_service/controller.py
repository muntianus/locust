"""
Deterministic B2B User Swarm Simulator & Live HTTP Probe Controller.

Models concurrent B2B user personas (`GloblexShopBuyerUser`, `GloblexProContractorUser`,
`GloblexOnlineAiCopilotUser`), calculates per-endpoint RPS, p50/p95/p99 latency,
Apdex score, and exports LaunchGate-compatible SLO reports.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from locustfile import PERSONA_SPECS, get_personas_catalog

DEFAULT_TARGET_HOST = "https://api.globlex.pro"
DEFAULT_APDEX_T_MS = 200.0
DEFAULT_SLO_P95_MS = 300.0
DEFAULT_SLO_ERROR_RATE = 0.01


def compute_percentiles(samples: Sequence[float]) -> Dict[str, float]:
    """Calculate deterministic p50, p95, p99, min, max, and mean latency from samples."""
    if not samples:
        return {"min_ms": 0.0, "mean_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "max_ms": 0.0}
    ordered = sorted(float(x) for x in samples)
    n = len(ordered)

    def _pct(p: float) -> float:
        if n == 1:
            return round(ordered[0], 2)
        idx = (p / 100.0) * (n - 1)
        lo = int(math.floor(idx))
        hi = int(math.ceil(idx))
        if lo == hi:
            return round(ordered[lo], 2)
        frac = idx - lo
        return round(ordered[lo] + (ordered[hi] - ordered[lo]) * frac, 2)

    return {
        "min_ms": round(ordered[0], 2),
        "mean_ms": round(sum(ordered) / n, 2),
        "p50_ms": _pct(50.0),
        "p95_ms": _pct(95.0),
        "p99_ms": _pct(99.0),
        "max_ms": round(ordered[-1], 2),
    }


def compute_apdex(samples: Sequence[float], target_t_ms: float = DEFAULT_APDEX_T_MS) -> float:
    """
    Calculate Apdex score:
      Satisfied: latency <= T
      Tolerating: T < latency <= 4T
      Frustrated: latency > 4T
      Apdex = (Satisfied + 0.5 * Tolerating) / Total
    """
    if not samples:
        return 1.0
    satisfied = 0
    tolerating = 0
    frustrated_threshold = 4.0 * float(target_t_ms)
    for val in samples:
        if val <= target_t_ms:
            satisfied += 1
        elif val <= frustrated_threshold:
            tolerating += 1
    score = (satisfied + 0.5 * tolerating) / len(samples)
    return round(max(0.0, min(1.0, score)), 4)


class SwarmController:
    """B2B User Swarm simulation and live HTTP probing engine."""

    def __init__(self, max_history: int = 100) -> None:
        self.max_history = max_history
        self._runs: List[Dict[str, Any]] = []

    def get_personas(self) -> Dict[str, Any]:
        personas = get_personas_catalog()
        total_weight = sum(int(p["weight"]) for p in personas)
        return {
            "service": "perfops-locust-swarm",
            "total_weight": total_weight,
            "personas": personas,
            "integrated_domains": [
                "globlex.shop",
                "globlex.pro",
                "globlex.online",
                "api.globlex.pro",
            ],
        }

    def get_runs(self, limit: int = 25) -> List[Dict[str, Any]]:
        return list(reversed(self._runs[-max(1, int(limit)) :]))

    def _normalize_weights(self, custom_weights: Optional[Dict[str, float]]) -> Dict[str, float]:
        raw: Dict[str, float] = {}
        for name, spec in PERSONA_SPECS.items():
            if custom_weights and name in custom_weights:
                val = float(custom_weights[name])
                if val < 0:
                    raise ValueError(f"Weight for persona {name} must be non-negative")
                raw[name] = val
            else:
                raw[name] = float(spec["weight"])
        total = sum(raw.values())
        if total <= 0:
            raise ValueError("Total persona weight must be greater than zero")
        return {k: v / total for k, v in raw.items()}

    def _probe_live_endpoint(
        self,
        target_host: str,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout_sec: float = 2.5,
        http_opener: Optional[Callable[..., Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{target_host.rstrip('/')}{path}"
        req_headers = {
            "User-Agent": "perfops-locust-swarm/1.0.0",
            "Accept": "application/json",
            **(headers or {}),
        }
        body_bytes: Optional[bytes] = None
        if payload is not None and method.upper() in ("POST", "PUT", "PATCH"):
            body_bytes = json.dumps(payload).encode("utf-8")
            req_headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url=url, data=body_bytes, headers=req_headers, method=method.upper())
        opener = http_opener or urllib.request.urlopen
        start = time.perf_counter()
        try:
            with opener(req, timeout=timeout_sec) as resp:
                _ = resp.read(512)
                status = getattr(resp, "status", 200)
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                return {
                    "ok": 200 <= int(status) < 400,
                    "status_code": int(status),
                    "latency_ms": round(elapsed_ms, 2),
                }
        except urllib.error.HTTPError as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            return {
                "ok": False,
                "status_code": int(exc.code),
                "latency_ms": round(elapsed_ms, 2),
                "error": f"HTTP {exc.code}",
            }
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            return {
                "ok": False,
                "status_code": 0,
                "latency_ms": round(elapsed_ms, 2),
                "error": str(exc),
            }

    def simulate(
        self,
        vus: int = 50,
        duration_sec: float = 30.0,
        target_host: str = DEFAULT_TARGET_HOST,
        persona_weights: Optional[Dict[str, float]] = None,
        mode: str = "deterministic",
        seed: Optional[int] = 42,
        apdex_t_ms: float = DEFAULT_APDEX_T_MS,
        slo_p95_ms: float = DEFAULT_SLO_P95_MS,
        http_opener: Optional[Callable[..., Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute a B2B user swarm simulation (or hybrid live probe) and compute
        per-endpoint RPS, p50/p95/p99 latency, Apdex score, and LaunchGate SLO report.
        """
        vus_int = int(vus)
        duration_f = float(duration_sec)
        if vus_int <= 0:
            raise ValueError("vus must be a positive integer")
        if duration_f <= 0:
            raise ValueError("duration_sec must be positive")
        if mode not in ("deterministic", "live_probe"):
            raise ValueError("mode must be 'deterministic' or 'live_probe'")

        normalized_weights = self._normalize_weights(persona_weights)
        seed_basis = f"{seed}:{vus_int}:{duration_f}:{target_host}:{ sorted(normalized_weights.items()) }"
        seed_int = int(hashlib.sha256(seed_basis.encode("utf-8")).hexdigest()[:12], 16)
        rng = random.Random(seed_int)

        # Concurrency pressure multiplier: gentle queueing curve as VUs scale up
        concurrency_factor = 1.0 + max(0.0, math.log10(max(1.0, vus_int / 10.0)) * 0.22)

        endpoint_results: List[Dict[str, Any]] = []
        persona_summaries: Dict[str, Dict[str, Any]] = {}
        all_latency_samples: List[float] = []
        total_requests = 0
        total_errors = 0

        for persona_name, spec in PERSONA_SPECS.items():
            share = normalized_weights.get(persona_name, 0.0)
            persona_vus = max(0.0, vus_int * share)
            avg_wait = sum(spec["wait_time_sec"]) / 2.0
            # Throughput per VU based on wait_time + average service response time (~0.1s)
            vu_cycle_sec = max(0.25, avg_wait + 0.1)
            persona_total_rps = persona_vus / vu_cycle_sec

            ep_weights_sum = sum(int(ep["weight"]) for ep in spec["endpoints"])
            persona_latencies: List[float] = []
            persona_reqs = 0
            persona_errs = 0

            for ep in spec["endpoints"]:
                ep_share = int(ep["weight"]) / max(1, ep_weights_sum)
                ep_rps = round(persona_total_rps * ep_share, 3)
                ep_requests = max(1, int(round(ep_rps * duration_f))) if share > 0 else 0

                base_ms = float(ep["base_latency_ms"]) * concurrency_factor
                jitter_ms = float(ep["jitter_ms"])

                live_probe_result: Optional[Dict[str, Any]] = None
                if mode == "live_probe" and ep_requests > 0:
                    live_probe_result = self._probe_live_endpoint(
                        target_host=target_host,
                        method=ep["method"],
                        path=ep["path"],
                        payload=ep.get("payload"),
                        headers={
                            "X-Globlex-Domain": spec["domain"],
                            "X-PerfOps-Persona": persona_name,
                        },
                        http_opener=http_opener,
                    )
                    if live_probe_result["latency_ms"] > 0:
                        base_ms = (base_ms * 0.6) + (float(live_probe_result["latency_ms"]) * 0.4)

                # Generate deterministic sample distribution (capped at 120 samples per endpoint for speed)
                sample_count = min(max(20, ep_requests), 120) if ep_requests > 0 else 0
                samples: List[float] = []
                for i in range(sample_count):
                    # Log-normal-like tail modeling for realistic p95/p99
                    u = rng.random()
                    tail_boost = 1.0 + (0.65 * (-math.log(max(1e-4, 1.0 - u))) ** 1.15) * (jitter_ms / max(base_ms, 1.0))
                    noise = rng.uniform(-0.35 * jitter_ms, 0.45 * jitter_ms)
                    lat = max(5.0, (base_ms * tail_boost) + noise)
                    samples.append(round(lat, 2))

                pcts = compute_percentiles(samples)
                ep_apdex = compute_apdex(samples, target_t_ms=apdex_t_ms)

                # Deterministic low error rate unless live_probe failed
                if live_probe_result is not None and not live_probe_result["ok"]:
                    ep_errors = max(1, int(round(ep_requests * 0.05)))
                else:
                    ep_errors = int(ep_requests * 0.0015) if vus_int > 250 else 0

                error_rate = round(ep_errors / max(1, ep_requests), 5) if ep_requests > 0 else 0.0

                all_latency_samples.extend(samples)
                persona_latencies.extend(samples)
                total_requests += ep_requests
                total_errors += ep_errors
                persona_reqs += ep_requests
                persona_errs += ep_errors

                ep_entry: Dict[str, Any] = {
                    "persona": persona_name,
                    "domain": spec["domain"],
                    "method": ep["method"],
                    "path": ep["path"],
                    "name": f"{ep['method']} {ep['path']}",
                    "weight": ep["weight"],
                    "requests": ep_requests,
                    "errors": ep_errors,
                    "error_rate": error_rate,
                    "rps": ep_rps,
                    "apdex": ep_apdex,
                    **pcts,
                }
                if live_probe_result is not None:
                    ep_entry["live_probe"] = live_probe_result
                endpoint_results.append(ep_entry)

            p_pcts = compute_percentiles(persona_latencies)
            persona_summaries[persona_name] = {
                "persona": persona_name,
                "domain": spec["domain"],
                "weight_share": round(share, 4),
                "allocated_vus": round(persona_vus, 2),
                "requests": persona_reqs,
                "errors": persona_errs,
                "rps": round(persona_reqs / duration_f, 3),
                "apdex": compute_apdex(persona_latencies, target_t_ms=apdex_t_ms),
                "slo_target_p95_ms": spec["slo_target_p95_ms"],
                "slo_pass": p_pcts["p95_ms"] <= float(spec["slo_target_p95_ms"]),
                **p_pcts,
            }

        overall_pcts = compute_percentiles(all_latency_samples)
        overall_apdex = compute_apdex(all_latency_samples, target_t_ms=apdex_t_ms)
        overall_rps = round(total_requests / duration_f, 3)
        overall_error_rate = round(total_errors / max(1, total_requests), 5)

        launchgate_report = self._build_launchgate_report(
            overall_pcts=overall_pcts,
            overall_apdex=overall_apdex,
            overall_rps=overall_rps,
            overall_error_rate=overall_error_rate,
            persona_summaries=persona_summaries,
            slo_p95_ms=slo_p95_ms,
            target_host=target_host,
        )

        run_payload: Dict[str, Any] = {
            "run_id": f"swarm-{uuid.uuid4().hex[:10]}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": "perfops-locust-swarm",
            "mode": mode,
            "config": {
                "vus": vus_int,
                "duration_sec": duration_f,
                "target_host": target_host,
                "seed": seed,
                "apdex_t_ms": apdex_t_ms,
                "slo_p95_ms": slo_p95_ms,
                "persona_weights": {k: round(v, 4) for k, v in normalized_weights.items()},
            },
            "summary": {
                "total_requests": total_requests,
                "total_errors": total_errors,
                "error_rate": overall_error_rate,
                "total_rps": overall_rps,
                "apdex": overall_apdex,
                **overall_pcts,
            },
            "personas": persona_summaries,
            "endpoints": endpoint_results,
            "launchgate": launchgate_report,
        }

        self._runs.append(run_payload)
        if len(self._runs) > self.max_history:
            self._runs = self._runs[-self.max_history :]

        return run_payload

    def _build_launchgate_report(
        self,
        overall_pcts: Dict[str, float],
        overall_apdex: float,
        overall_rps: float,
        overall_error_rate: float,
        persona_summaries: Dict[str, Dict[str, Any]],
        slo_p95_ms: float,
        target_host: str,
    ) -> Dict[str, Any]:
        checks = [
            {
                "metric": "http_req_duration_p95_ms",
                "actual": overall_pcts["p95_ms"],
                "threshold": float(slo_p95_ms),
                "operator": "<=",
                "passed": overall_pcts["p95_ms"] <= float(slo_p95_ms),
            },
            {
                "metric": "http_req_failed_rate",
                "actual": overall_error_rate,
                "threshold": DEFAULT_SLO_ERROR_RATE,
                "operator": "<=",
                "passed": overall_error_rate <= DEFAULT_SLO_ERROR_RATE,
            },
            {
                "metric": "apdex_score",
                "actual": overall_apdex,
                "threshold": 0.85,
                "operator": ">=",
                "passed": overall_apdex >= 0.85,
            },
        ]
        all_passed = all(bool(c["passed"]) for c in checks)
        return {
            "schema_version": "launchgate-core/v1",
            "source": "perfops-locust-swarm",
            "target_host": target_host,
            "ready": all_passed,
            "verdict": "PASS" if all_passed else "FAIL",
            "total_rps": overall_rps,
            "p95_ms": overall_pcts["p95_ms"],
            "p99_ms": overall_pcts["p99_ms"],
            "apdex": overall_apdex,
            "error_rate": overall_error_rate,
            "checks": checks,
            "domain_readiness": {
                info["domain"]: info["slo_pass"] for info in persona_summaries.values()
            },
        }

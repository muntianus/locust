"""
PerfOps B2B User Swarm & Distributed Load Scenarios (Locust + Standalone Compatible).

Supports both standard `locust -f locustfile.py` CLI execution and zero-dependency
standalone import by `swarm_service` and automated unit tests.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from locust import HttpUser, between, task  # type: ignore
    LOCUST_AVAILABLE = True
except ImportError:
    LOCUST_AVAILABLE = False

    def between(min_wait: float, max_wait: float) -> Callable[[], float]:
        """Fallback wait_time generator when locust package is not installed."""
        def _wait() -> float:
            return (float(min_wait) + float(max_wait)) / 2.0

        _wait.min_wait = min_wait  # type: ignore[attr-defined]
        _wait.max_wait = max_wait  # type: ignore[attr-defined]
        return _wait

    def task(weight: Any = 1) -> Any:
        """Fallback @task decorator preserving task weight metadata."""
        if callable(weight):
            func = weight
            setattr(func, "locust_task_weight", 1)
            return func

        def _decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            setattr(func, "locust_task_weight", int(weight))
            return func

        return _decorator

    class _DummyResponse:
        def __init__(self, status_code: int = 200, url: str = "") -> None:
            self.status_code = status_code
            self.url = url

        def __enter__(self) -> "_DummyResponse":
            return self

        def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
            return None

        def success(self) -> None:
            pass

        def failure(self, message: str) -> None:
            pass

    class _DummyClient:
        """Lightweight client recorder for standalone execution and unit testing."""

        def __init__(self, base_url: str = "") -> None:
            self.base_url = base_url.rstrip("/")
            self.history: List[Dict[str, Any]] = []

        def request(
            self,
            method: str,
            path: str,
            name: Optional[str] = None,
            json: Optional[Dict[str, Any]] = None,
            headers: Optional[Dict[str, str]] = None,
            catch_response: bool = False,
            **kwargs: Any,
        ) -> _DummyResponse:
            record = {
                "method": method.upper(),
                "path": path,
                "name": name or path,
                "json": json,
                "headers": headers or {},
                "catch_response": catch_response,
            }
            self.history.append(record)
            return _DummyResponse(status_code=200, url=f"{self.base_url}{path}")

        def get(self, path: str, **kwargs: Any) -> _DummyResponse:
            return self.request("GET", path, **kwargs)

        def post(self, path: str, **kwargs: Any) -> _DummyResponse:
            return self.request("POST", path, **kwargs)

    class HttpUser:  # type: ignore[no-redef]
        """Fallback HttpUser base class when locust is not installed."""

        weight: int = 1
        host: str = "https://api.globlex.pro"
        wait_time: Any = between(1, 3)

        def __init__(self, host: Optional[str] = None, client: Optional[Any] = None) -> None:
            if host:
                self.host = host
            self.client = client if client is not None else _DummyClient(self.host)


# Structured endpoint specifications for each B2B persona
PERSONA_SPECS: Dict[str, Dict[str, Any]] = {
    "GloblexShopBuyerUser": {
        "name": "GloblexShopBuyerUser",
        "weight": 50,
        "domain": "globlex.shop",
        "upstream": "api.globlex.pro",
        "description": "B2B procurement buyer browsing verified industrial suppliers, parsing RFQ quotes, and checking enterprise overview.",
        "wait_time_sec": [0.8, 2.2],
        "slo_target_p95_ms": 240.0,
        "endpoints": [
            {
                "path": "/companies/",
                "method": "GET",
                "weight": 5,
                "base_latency_ms": 62.0,
                "jitter_ms": 18.0,
                "description": "Browse B2B industrial supplier directory",
            },
            {
                "path": "/quote-parse/",
                "method": "POST",
                "weight": 3,
                "base_latency_ms": 128.0,
                "jitter_ms": 34.0,
                "payload": {
                    "rfq_id": "RFQ-2026-8841",
                    "items": [
                        {"sku": "ST-PIPE-159", "qty": 120, "unit": "m"},
                        {"sku": "FLANGE-DN150", "qty": 40, "unit": "pcs"},
                    ],
                    "currency": "RUB",
                },
                "description": "Parse structured B2B commercial quote lines",
            },
            {
                "path": "/enterprise/overview",
                "method": "GET",
                "weight": 2,
                "base_latency_ms": 84.0,
                "jitter_ms": 22.0,
                "description": "Check buyer enterprise procurement KPIs",
            },
        ],
    },
    "GloblexProContractorUser": {
        "name": "GloblexProContractorUser",
        "weight": 30,
        "domain": "globlex.pro",
        "upstream": "api.globlex.pro",
        "description": "Enterprise contractor inspecting active tenders, running INN counterparty compliance checks, and querying Master-OS telemetry.",
        "wait_time_sec": [1.0, 2.8],
        "slo_target_p95_ms": 260.0,
        "endpoints": [
            {
                "path": "/enterprise/tenders",
                "method": "GET",
                "weight": 4,
                "base_latency_ms": 78.0,
                "jitter_ms": 20.0,
                "description": "Inspect enterprise B2B tender pipeline",
            },
            {
                "path": "/inn-check/6671094821",
                "method": "GET",
                "weight": 3,
                "base_latency_ms": 95.0,
                "jitter_ms": 25.0,
                "description": "Verify counterparty INN 6671094821 KYB/compliance score",
            },
            {
                "path": "/enterprise/master-os/overview",
                "method": "GET",
                "weight": 3,
                "base_latency_ms": 110.0,
                "jitter_ms": 28.0,
                "description": "Query Master-OS operational control plane overview",
            },
        ],
    },
    "GloblexOnlineAiCopilotUser": {
        "name": "GloblexOnlineAiCopilotUser",
        "weight": 20,
        "domain": "globlex.online",
        "upstream": "api.globlex.pro",
        "description": "AI procurement copilot user sending B2B supply-chain analysis prompts and polling service health.",
        "wait_time_sec": [1.2, 3.2],
        "slo_target_p95_ms": 350.0,
        "endpoints": [
            {
                "path": "/ai-chat/",
                "method": "POST",
                "weight": 3,
                "base_latency_ms": 165.0,
                "jitter_ms": 45.0,
                "payload": {
                    "session_id": "copilot-b2b-swarm",
                    "prompt": "Compare supplier lead times and counterparty risk for INN 6671094821",
                    "context": {"domain": "globlex.online", "mode": "procurement_audit"},
                },
                "description": "Submit B2B procurement copilot prompt to /ai-chat/",
            },
            {
                "path": "/health",
                "method": "GET",
                "weight": 2,
                "base_latency_ms": 24.0,
                "jitter_ms": 8.0,
                "description": "Check AI copilot & gateway health status",
            },
        ],
    },
}


class GloblexShopBuyerUser(HttpUser):
    """
    B2B Marketplace Buyer persona (`globlex.shop`, weight 50).
    Browses `/companies/`, parses quotes `/quote-parse/`, and checks `/enterprise/overview`.
    """

    weight = 50
    wait_time = between(0.8, 2.2)
    persona_meta = PERSONA_SPECS["GloblexShopBuyerUser"]

    @task(5)
    def browse_companies(self) -> Any:
        return self.client.get(
            "/companies/",
            name="GET /companies/",
            headers={"X-Globlex-Domain": "globlex.shop", "X-PerfOps-Persona": "GloblexShopBuyerUser"},
        )

    @task(3)
    def parse_quote(self) -> Any:
        payload = self.persona_meta["endpoints"][1]["payload"]
        return self.client.post(
            "/quote-parse/",
            name="POST /quote-parse/",
            json=payload,
            headers={"X-Globlex-Domain": "globlex.shop", "X-PerfOps-Persona": "GloblexShopBuyerUser"},
        )

    @task(2)
    def enterprise_overview(self) -> Any:
        return self.client.get(
            "/enterprise/overview",
            name="GET /enterprise/overview",
            headers={"X-Globlex-Domain": "globlex.shop", "X-PerfOps-Persona": "GloblexShopBuyerUser"},
        )


class GloblexProContractorUser(HttpUser):
    """
    B2B Enterprise Contractor persona (`globlex.pro`, weight 30).
    Inspects `/enterprise/tenders`, checks counterparty `/inn-check/6671094821`,
    and queries `/enterprise/master-os/overview`.
    """

    weight = 30
    wait_time = between(1.0, 2.8)
    persona_meta = PERSONA_SPECS["GloblexProContractorUser"]

    @task(4)
    def inspect_tenders(self) -> Any:
        return self.client.get(
            "/enterprise/tenders",
            name="GET /enterprise/tenders",
            headers={"X-Globlex-Domain": "globlex.pro", "X-PerfOps-Persona": "GloblexProContractorUser"},
        )

    @task(3)
    def verify_counterparty_inn(self) -> Any:
        return self.client.get(
            "/inn-check/6671094821",
            name="GET /inn-check/6671094821",
            headers={"X-Globlex-Domain": "globlex.pro", "X-PerfOps-Persona": "GloblexProContractorUser"},
        )

    @task(3)
    def master_os_overview(self) -> Any:
        return self.client.get(
            "/enterprise/master-os/overview",
            name="GET /enterprise/master-os/overview",
            headers={"X-Globlex-Domain": "globlex.pro", "X-PerfOps-Persona": "GloblexProContractorUser"},
        )


class GloblexOnlineAiCopilotUser(HttpUser):
    """
    B2B AI Copilot User persona (`globlex.online`, weight 20).
    Sends prompts to `/ai-chat/` and checks `/health`.
    """

    weight = 20
    wait_time = between(1.2, 3.2)
    persona_meta = PERSONA_SPECS["GloblexOnlineAiCopilotUser"]

    @task(3)
    def send_ai_chat_prompt(self) -> Any:
        payload = self.persona_meta["endpoints"][0]["payload"]
        return self.client.post(
            "/ai-chat/",
            name="POST /ai-chat/",
            json=payload,
            headers={
                "X-Globlex-Domain": "globlex.online",
                "X-PerfOps-Persona": "GloblexOnlineAiCopilotUser",
            },
        )

    @task(2)
    def check_health(self) -> Any:
        return self.client.get(
            "/health",
            name="GET /health",
            headers={
                "X-Globlex-Domain": "globlex.online",
                "X-PerfOps-Persona": "GloblexOnlineAiCopilotUser",
            },
        )


B2B_USER_CLASSES: Tuple[Any, ...] = (
    GloblexShopBuyerUser,
    GloblexProContractorUser,
    GloblexOnlineAiCopilotUser,
)


def get_personas_catalog() -> List[Dict[str, Any]]:
    """Return serializable catalog of B2B personas and endpoint weights."""
    catalog: List[Dict[str, Any]] = []
    for cls in B2B_USER_CLASSES:
        spec = PERSONA_SPECS[cls.__name__]
        total_ep_weight = sum(int(ep["weight"]) for ep in spec["endpoints"])
        endpoints = []
        for ep in spec["endpoints"]:
            ep_copy = dict(ep)
            ep_copy["relative_share"] = round(int(ep["weight"]) / max(total_ep_weight, 1), 4)
            endpoints.append(ep_copy)
        catalog.append(
            {
                "persona": cls.__name__,
                "weight": cls.weight,
                "domain": spec["domain"],
                "upstream": spec["upstream"],
                "description": spec["description"],
                "wait_time_sec": spec["wait_time_sec"],
                "slo_target_p95_ms": spec["slo_target_p95_ms"],
                "endpoints": endpoints,
            }
        )
    return catalog

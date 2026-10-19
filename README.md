# PerfOps Locust — B2B User Swarm & Distributed Load Service

Python B2B User Swarm & Traffic Simulation Service for the PerfOps / Globex ecosystem (`globlex.shop`, `globlex.pro`, `globlex.online`, `api.globlex.pro`, and `launchgate-core`).

Supports both:
1. **Standard Locust CLI execution** (`locust -f locustfile.py`)
2. **Standalone Zero-Dependency Python HTTP Swarm Controller API** (`python3 -m swarm_service.server`, default `PORT=4070`)

---

## B2B User Swarm Personas (`locustfile.py`)

| Persona | Weight | Domain | Target Endpoints | SLO p95 |
| :--- | :---: | :--- | :--- | :---: |
| `GloblexShopBuyerUser` | `50` | `globlex.shop` | `GET /companies/`, `POST /quote-parse/`, `GET /enterprise/overview` | `240 ms` |
| `GloblexProContractorUser` | `30` | `globlex.pro` | `GET /enterprise/tenders`, `GET /inn-check/6671094821`, `GET /enterprise/master-os/overview` | `260 ms` |
| `GloblexOnlineAiCopilotUser` | `20` | `globlex.online` | `POST /ai-chat/`, `GET /health` | `350 ms` |

---

## HTTP Swarm Controller API (`swarm_service/server.py`)

Start the standalone service (requires only the Python standard library):

```bash
PORT=4070 python3 -m swarm_service.server
```

### Endpoints

- `GET /health`
  Returns `{"status": "ok", "service": "perfops-locust-swarm", "version": "1.0.0"}`.
- `GET /api/v1/personas`
  Returns the 3 B2B user personas, domain mappings, wait times, and endpoint weights.
- `POST /api/v1/swarm/simulate`
  Runs a deterministic or live-probe B2B user swarm simulation and returns per-endpoint RPS, `p50`/`p95`/`p99` latency, Apdex score, and a `launchgate-core/v1` readiness payload:
  ```bash
  curl -X POST http://127.0.0.1:4070/api/v1/swarm/simulate \
    -H "Content-Type: application/json" \
    -d '{"vus": 100, "duration_sec": 30, "target_host": "https://api.globlex.pro"}'
  ```
- `GET /api/v1/swarm/runs`
  Lists recent swarm simulation runs stored in memory.

---

## Running with Locust CLI

```bash
pip install locust
locust -f locustfile.py --headless -u 100 -r 10 -t 30s --host https://api.globlex.pro
```

---

## Testing

```bash
# Unit + HTTP integration tests
python3 -m unittest discover -s tests -v

# End-to-end smoke: boots a local mock target + the swarm service on loopback
python3 .github/scripts/ci_smoke.py

# Optional: smoke a real environment instead of the local mock
TARGET_HOST=https://api.globlex.pro python3 .github/scripts/ci_smoke.py
```

The smoke driver (`.github/scripts/ci_smoke.py`) is offline by default: it starts
`tools/mock_target.py`, which answers every route in `locustfile.PERSONA_SPECS` with
`200 OK`, then runs the swarm in both `deterministic` and `live_probe` modes against
`http://127.0.0.1:<port>`. No external domain is contacted unless `TARGET_HOST` (or the
k6-style alias `__ENV.TARGET_HOST`) is set. Ports are ephemeral, so the smoke can run on
the same host as the production service (`:4070`).

Mock target, standalone:

```bash
python3 tools/mock_target.py --port 18080            # serve on :18080
python3 tools/mock_target.py --port 0 --print-port    # ephemeral port
python3 tools/mock_target.py --delay-ms 250           # emulate a slow target
```

Smoke knobs (all optional): `SMOKE_VUS` (default `10`), `SMOKE_DURATION_SEC` (`5`),
`SMOKE_SLO_P95_MS` (`5000`), `SMOKE_APDEX_T_MS` (`5000`), `SMOKE_READY_TIMEOUT_SEC` (`60`),
`MOCK_DELAY_MS` (`0`). Thresholds are intentionally slack — the smoke is a liveness and
contract check, not a latency benchmark.

## Contact / Support

PerfOps official Telegram contact: [@perfops_ai_manager](https://t.me/perfops_ai_manager).

For security vulnerabilities, use private vulnerability reporting rather than public Telegram messages.

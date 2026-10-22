# AGENTS.md — locust

Org-wide rules: [`muntianus/.github/AGENT_POLICY.md`](https://github.com/muntianus/.github/blob/main/AGENT_POLICY.md)
and the [Repository Standard](https://github.com/muntianus/.github/blob/main/docs/standards/REPOSITORY_STANDARD.md).

## Repo-local rules

- **Zero dependencies for the service.** `swarm_service/` and `tools/` use the Python standard library only; `locustfile.py` needs the `locust` package and is not imported by tests.
- **Contract first.** A route change updates `ROUTES` in `swarm_service/server.py` and
  `docs/api/openapi.json` in the same commit; `tests/test_openapi_contract.py` fails on drift.
- **Config is env-only.** Every variable the code reads is listed in `.env.example`;
  never commit `.env`.
- **`GET /health` stays dependency-free** (liveness only).
- **Simulation is deterministic by default.** `mode: deterministic` never touches the network and is seeded; keep `seed` reproducibility covered by tests.
- `.github/scripts/ci_smoke.py` runs the swarm against `tools/mock_target.py` in CI — keep it working without network access.
- Before pushing: `python3 -m unittest discover -s tests -v`.
- Conventional commits; branch `feat/*` / `fix/*` / `chore/*`; never push to `main`.

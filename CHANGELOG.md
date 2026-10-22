# Changelog

Notable changes to this service. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Security

- `POST /api/v1/swarm/simulate` with `mode: live_probe` only probes `target_host`s whose host is
  in `SWARM_ALLOWED_TARGET_HOSTS` (default: public globlex.* / perfops.* hosts) over http(s), and
  no longer follows redirects; anything else is 403 `forbidden_target`. Previously any caller
  could make the service probe internal addresses and report status codes and latency.

### Fixed

- `GET /api/v1/swarm/runs?limit=<non-integer>` falls back to 25 instead of dropping the connection.

### Changed

- Adopted the muntianus Repository Standard v1: committed OpenAPI contract
  (`docs/api/openapi.json`) with a route/spec drift test, `AGENTS.md` / `CLAUDE.md` /
  `SECURITY.md`, `.env.example`, Dependabot, a non-root Docker image with a health check,
  and CI with SHA-pinned actions, least-privilege permissions and the org security gate.

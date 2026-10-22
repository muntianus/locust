# Security Policy

locust is an internal PerfOps microservice (default port 4070).

- **Report a vulnerability:** open a private security advisory on this repo
  (GitHub → Security → Advisories) or contact the owner listed in
  [`muntianus/.github`](https://github.com/muntianus/.github/blob/main/SECURITY.md).
- **Secrets:** none are required. Never commit `.env`; only
  `.env.example` (placeholder values) is tracked. CI runs gitleaks via the org security gate.
- The API is unauthenticated and sends `Access-Control-Allow-Origin: *`. `mode: live_probe` makes outbound requests to the caller's `target_host`; keep the service on loopback / behind Caddy.

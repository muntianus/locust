# Security Policy

locust is an internal PerfOps microservice (default port 4070).

- **Report a vulnerability:** open a private security advisory on this repo
  (GitHub → Security → Advisories) or contact the owner listed in
  [`muntianus/.github`](https://github.com/muntianus/.github/blob/main/SECURITY.md).
- **Secrets:** none are required. Never commit `.env`; only
  `.env.example` (placeholder values) is tracked. GitGuardian scans every pull request for
  secrets.
- The API is unauthenticated and sends `Access-Control-Allow-Origin: *`. `mode: live_probe`
  makes outbound requests, so `target_host` must be http(s) and its host must be in
  `SWARM_ALLOWED_TARGET_HOSTS` (default: the portfolio's public hosts); redirects are not
  followed. Never add internal hosts or IPs; keep the service on loopback / behind Caddy.

# Security Policy

## Reporting a vulnerability
Please report privately via GitHub Security Advisories ("Report a vulnerability" on the Security tab). Do not open public issues for vulnerabilities. Expect an acknowledgement within 72 hours.

## Built-in controls
- Write endpoints are **disabled** unless `API_KEY` is set; keys are compared in constant time.
- Parameterised SQL everywhere; Jinja autoescaping for all HTML output; strict CSP, `X-Frame-Options: DENY`, `nosniff`, `no-store`.
- Request-size limit and per-IP rate limiting (per worker process).
- Container runs as non-root with a read-only root filesystem and all capabilities dropped; Kubernetes manifests mirror this.
- CI: Trivy image scan, CodeQL, Dependabot.

## Known limitations
- Read-only pages and `/metrics` are unauthenticated: deploy behind an authenticating reverse proxy/ingress or keep the Service cluster-internal.
- SQLite storage means a single replica; rate limiting is in-memory and per worker.
- No TLS termination in the app; terminate TLS at your ingress or proxy.

# Security Policy

## Supported Versions

This project is pre-1.0. Security fixes target the current `master` branch until a formal release branch policy exists.

## Reporting a Vulnerability

Please do not open a public issue for a vulnerability.

Send a private report to the project maintainer with:

- Affected component or endpoint.
- Steps to reproduce.
- Impact and expected attacker capability.
- Any suggested fix or mitigation.

If no private security contact is configured for your fork, use GitHub private vulnerability reporting before publishing the issue publicly.

## Security Expectations

Reports are especially helpful around:

- API-key verification and storage.
- Session ownership and cookie isolation.
- Proxy credential handling.
- Stripe webhook verification.
- License verification bypass.
- SSRF or unsafe URL handling.

## URL Safety & SSRF Controls (post-v1, issue #8)

The `/v1/scrape` endpoint enforces:

- Only `http` and `https` schemes are accepted (rejected with `INVALID_URL_SCHEME` *before* any browser/worker/proxy execution).
- Optional `security.block_private_networks: true` (recommended for cloud/public deployments) blocks localhost, RFC1918 private ranges, link-local, and common metadata endpoints (169.254.169.254, metadata.google.internal, etc.).

**Deployment recommendations (cloud mode):**
- Enable `block_private_networks: true` in `config.yaml`.
- Enforce egress firewall / VPC rules blocking RFC1918 + metadata services.
- Run containers as non-root with appropriate seccomp/AppArmor profiles.
- Monitor for `PRIVATE_NETWORK_BLOCKED` error responses.

Log redaction (via `security.*` config):
- `log_url_domains_only: true`: only host:port appears in usage records / redacted log helpers.
- `redact_authorization_headers: true`: Authorization, cookies, X-API-Key etc. become `[REDACTED]`.

Implementation and tests live in `asb_api/security.py` + `tests/test_security.py`.


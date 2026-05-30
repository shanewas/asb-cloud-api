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


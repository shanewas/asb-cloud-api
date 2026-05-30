# ASB Cloud API Clients

Thin, official clients for the ASB Cloud API v1. These make first integration fast while keeping the REST API as the source of truth.

## Package Names & Versioning

- **Python**: `asb-cloud-client` (import: `asb_client`)
- **Node.js / TypeScript**: `asb-cloud-client`
- **CLI**: `asb-cli` (command: `asb`)

All clients follow the same semantic versioning as the API (e.g. 0.1.x for API 0.1.x). Breaking changes in the stable v1 surface will bump major versions together.

## Repository Layout

```
clients/
  README.md
  python/           # Python SDK (asb-cloud-client)
    pyproject.toml
    asb_client/
      __init__.py
      client.py
      exceptions.py
      ...
  node/             # Node SDK (asb-cloud-client)
    package.json
    index.js
    ...
  cli/              # CLI tool (asb-cli)
    pyproject.toml
    asb_cli/
      ...
examples/
  python/
  node/
  cli/
```

## Scope (v1 clients)

- `POST /v1/scrape`
- `POST /v1/sessions`, `GET /v1/sessions/{id}`, `DELETE /v1/sessions/{id}`
- `GET /v1/usage`
- `GET /v1/billing/portal` (billing portal link)
- `GET /v1/health` (convenience, no auth)

Error handling, auth (Bearer), timeouts, and basic retry on transient errors are included.

## Local Development

See the per-client READMEs and the examples/ directory. All examples are designed to run against a locally running self-hosted API (in-memory mode is sufficient for smoke tests).

## Release Process

1. Update server + all clients for new stable surface changes.
2. Bump versions consistently in pyproject.json / package.json.
3. Update examples and this doc.
4. Tag and publish each package independently (PyPI, npm, PyPI for CLI).
5. Announce in CHANGELOG.md (root).

See main [README.md](../README.md) for API details.

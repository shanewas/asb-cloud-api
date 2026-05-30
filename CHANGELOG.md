# Changelog

All notable changes to this project will be documented here.

The format follows the spirit of Keep a Changelog, and this project uses semantic versioning after the first tagged release.

## Unreleased

### Added

- Release-candidate specification in `SPEC.md`.
- OSS-facing README, contribution, security, code of conduct, license, and package metadata.
- Regression tests for worker pool release behavior, proxy lease cleanup, and POST body serialization.
- `GET /v1/usage` route with in-memory and PostgreSQL usage tracker support.

### Fixed

- POST scrape requests now send request bodies instead of falling through to GET navigation.
- Region worker pools now release the actual acquired semaphore.
- Proxy leases are released when setup fails before browser execution.
- Screenshot directories are created before capture.
- PostgreSQL timestamp comparisons now use timezone-aware `datetime` values.
- PostgreSQL API-key records no longer rely on dict-style `.get()` access.
- Session endpoints and session-backed scrape requests now enforce key ownership.
- Optional PostgreSQL and Stripe modules are lazy-loaded so local in-memory mode can import without those extras installed.
- Shutdown now stops provider health checks and browser workers before closing the database.

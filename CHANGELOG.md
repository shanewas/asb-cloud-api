# Changelog

All notable changes to this project will be documented here.

The format follows the spirit of Keep a Changelog, and this project uses semantic versioning after the first tagged release.

## Unreleased

### Added

- Release-candidate specification in `SPEC.md`.
- OSS-facing README, contribution, security, code of conduct, license, and package metadata.
- Regression tests for worker pool release behavior, proxy lease cleanup, and POST body serialization.

### Fixed

- POST scrape requests now send request bodies instead of falling through to GET navigation.
- Region worker pools now release the actual acquired semaphore.
- Proxy leases are released when setup fails before browser execution.
- Screenshot directories are created before capture.
- PostgreSQL timestamp comparisons now use timezone-aware `datetime` values.
- PostgreSQL API-key records no longer rely on dict-style `.get()` access.


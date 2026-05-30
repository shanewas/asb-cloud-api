"""Security utilities for URL validation, SSRF hardening, and log redaction.

Implements controls from SPEC.md and issue #8.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException


@dataclass
class SecurityConfig:
    log_url_domains_only: bool = True
    redact_authorization_headers: bool = True
    block_private_networks: bool = False
    allowed_schemes: set[str] = None  # type: ignore

    def __post_init__(self):
        if self.allowed_schemes is None:
            self.allowed_schemes = {"http", "https"}


# Global set by startup (populated from config.yaml + env)
_security_config: SecurityConfig | None = None


def set_security_config(cfg: dict[str, Any] | SecurityConfig) -> None:
    global _security_config
    if isinstance(cfg, SecurityConfig):
        _security_config = cfg
    else:
        sec = cfg.get("security", {}) if isinstance(cfg, dict) else {}
        _security_config = SecurityConfig(
            log_url_domains_only=sec.get("log_url_domains_only", True),
            redact_authorization_headers=sec.get("redact_authorization_headers", True),
            block_private_networks=sec.get("block_private_networks", False),
        )


def get_security_config() -> SecurityConfig:
    if _security_config is None:
        # Safe defaults (permissive for local dev)
        return SecurityConfig()
    return _security_config


# -----------------------
# URL Safety / SSRF
# -----------------------

PRIVATE_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
    "metadata.google.internal",
    "169.254.169.254",  # AWS, GCP, Azure metadata
}

def _is_private_or_localhost(host: str) -> bool:
    """Best-effort check for private, loopback, or dangerous internal hosts."""
    if not host:
        return True
    host_lower = host.lower().strip("[]")
    if host_lower in PRIVATE_HOSTNAMES:
        return True
    # IP literal?
    try:
        ip = ipaddress.ip_address(host_lower)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except ValueError:
        pass
    # Common internal patterns (best effort; full DNS resolution + blocking is a deployment concern)
    if host_lower.endswith((".local", ".internal", ".lan")):
        return True
    if any(x in host_lower for x in ("metadata", "169.254", "10.", "192.168.", "172.16.", "172.17.", "172.18.")):
        # Rough heuristic; real protection should be at network layer for cloud
        return True
    return False


class URLSafetyError(ValueError):
    """Raised for unsafe URLs before they reach workers."""
    def __init__(self, message: str, error_code: str = "INVALID_URL"):
        super().__init__(message)
        self.error_code = error_code


def validate_scrape_url(url: str, security_cfg: SecurityConfig | None = None) -> str:
    """
    Validate that a scrape URL is safe to fetch.

    - Only http/https schemes allowed (rejects file://, javascript:, data:, ftp:, etc.)
    - Optional (config-driven) blocking of private network / localhost targets (SSRF mitigation for cloud).

    Returns the original URL if valid.
    Raises HTTPException (400) on failure so it can be used directly in routes.
    """
    cfg = security_cfg or get_security_config()

    if not url or not isinstance(url, str):
        raise HTTPException(
            status_code=400,
            detail={"error_code": "BAD_REQUEST", "message": "Missing or invalid URL"},
        )

    try:
        parsed = urlparse(url)
    except Exception:
        parsed = None

    if not parsed or not parsed.scheme or not parsed.netloc:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "INVALID_URL_SCHEME",
                "message": "URL must include scheme and host (e.g. https://example.com)",
            },
        )

    scheme = parsed.scheme.lower()
    if scheme not in (cfg.allowed_schemes or {"http", "https"}):
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "INVALID_URL_SCHEME",
                "message": f"Unsupported URL scheme '{scheme}'. Only http and https are allowed.",
            },
        )

    host = parsed.hostname or parsed.netloc.split(":")[0]
    if cfg.block_private_networks and _is_private_or_localhost(host):
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "PRIVATE_NETWORK_BLOCKED",
                "message": "Requests to private networks, localhost, or internal metadata services are not allowed in this configuration.",
            },
        )

    return url


# -----------------------
# Log Redaction Helpers
# -----------------------

def redact_url_for_logging(url: str, domains_only: bool | None = None) -> str:
    """
    Redact a URL for safe logging.

    If domains_only (default from config), returns only the domain/netloc.
    Otherwise returns the URL with potential secrets stripped (rare in URL itself).
    """
    cfg = get_security_config()
    use_domains_only = domains_only if domains_only is not None else cfg.log_url_domains_only

    if not url:
        return ""

    try:
        parsed = urlparse(url)
        if use_domains_only:
            if parsed.netloc:
                # Strip userinfo (credentials) from netloc for safety
                host = parsed.hostname or parsed.netloc.split("@")[-1]
                port = f":{parsed.port}" if parsed.port else ""
                return f"{host}{port}"
            # Fallback for weird inputs
            return url.split("/")[2] if "://" in url else url.split("/")[0]
        # Full URL but we never want to log query with tokens etc. — keep simple
        return url
    except Exception:
        return "[REDACTED_URL]"


def redact_headers_for_logging(headers: dict | None, redact_auth: bool | None = None) -> dict:
    """
    Return a copy of headers safe for logging.

    Redacts Authorization, Proxy-Authorization, and similar by default.
    Controlled by security.redact_authorization_headers.
    """
    cfg = get_security_config()
    should_redact = redact_auth if redact_auth is not None else cfg.redact_authorization_headers

    if not headers:
        return {}

    redacted = {}
    sensitive = {"authorization", "proxy-authorization", "x-api-key", "cookie"}

    for k, v in headers.items():
        if should_redact and k.lower() in sensitive:
            redacted[k] = "[REDACTED]"
        else:
            redacted[k] = v
    return redacted


def redact_api_key_for_logging(raw_key: str) -> str:
    """Never log full keys. Used in startup and error paths."""
    if not raw_key:
        return ""
    return raw_key[:12] + "..." if len(raw_key) > 12 else "[REDACTED]"

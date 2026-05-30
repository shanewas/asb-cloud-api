"""Thin ASB Cloud API client for Python."""

from __future__ import annotations

import os
from typing import Any, Literal, Optional
from urllib.parse import urljoin

import httpx

from .exceptions import (
    AsbAuthError,
    AsbError,
    AsbNotFoundError,
    AsbOverageError,
    AsbRateLimitError,
)


class AsbClient:
    """
    Thin synchronous + asynchronous client for the ASB Cloud API.

    Usage (sync):
        client = AsbClient(base_url="http://localhost:8000", api_key="sk_...")
        result = client.scrape(url="https://example.com")

    Usage (async):
        async with AsbClient(...) as client:
            result = await client.scrape_async(...)
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: str | None = None,
        timeout: float = 60.0,
        headers: dict[str, str] | None = None,
    ):
        self.base_url = base_url.rstrip("/") + "/"
        self.api_key = api_key or os.getenv("ASB_API_KEY")
        if not self.api_key:
            # Allow unauthenticated for health() only; other calls will fail
            pass

        self._timeout = timeout
        self._default_headers = {
            "User-Agent": "asb-cloud-client/0.1.0 (python)",
            **(headers or {}),
        }
        if self.api_key:
            self._default_headers["Authorization"] = f"Bearer {self.api_key}"

        # Lazy clients
        self._sync_client: httpx.Client | None = None
        self._async_client: httpx.AsyncClient | None = None

    # -----------------------
    # Context managers
    # -----------------------

    def __enter__(self) -> AsbClient:
        self._get_sync_client()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    async def __aenter__(self) -> AsbClient:
        await self._get_async_client()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclose()

    def close(self):
        if self._sync_client:
            self._sync_client.close()
            self._sync_client = None

    async def aclose(self):
        if self._async_client:
            await self._async_client.aclose()
            self._async_client = None

    # -----------------------
    # Internal HTTP helpers
    # -----------------------

    def _get_sync_client(self) -> httpx.Client:
        if self._sync_client is None:
            self._sync_client = httpx.Client(
                base_url=self.base_url,
                headers=self._default_headers,
                timeout=self._timeout,
            )
        return self._sync_client

    async def _get_async_client(self) -> httpx.AsyncClient:
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._default_headers,
                timeout=self._timeout,
            )
        return self._async_client

    def _url(self, path: str) -> str:
        return urljoin(self.base_url, path.lstrip("/"))

    def _handle_error(self, resp: httpx.Response) -> None:
        if 200 <= resp.status_code < 300:
            return

        status = resp.status_code
        try:
            data = resp.json()
        except Exception:
            data = {"message": resp.text or f"HTTP {status}"}

        message = data.get("message") or data.get("detail") or str(data)
        if isinstance(data.get("detail"), dict):
            message = data["detail"].get("message", message)
            error_code = data["detail"].get("error_code")
        else:
            error_code = data.get("error_code")

        if status == 403 and ("auth" in message.lower() or "key" in message.lower() or not self.api_key):
            raise AsbAuthError(message, status_code=status, error_code=error_code, response=data)
        if status == 404:
            raise AsbNotFoundError(message, status_code=status, error_code=error_code, response=data)
        if status == 429:
            detail = data.get("detail", data)
            raise AsbRateLimitError(
                message,
                status_code=status,
                error_code=error_code or "RATE_LIMIT_EXCEEDED",
                limit=detail.get("limit"),
                remaining=detail.get("remaining"),
                reset_at=detail.get("reset_at"),
                response=data,
            )
        if status == 402:
            detail = data.get("detail", data)
            raise AsbOverageError(
                message,
                status_code=status,
                overage_cost_usd=detail.get("overage_cost_usd"),
                response=data,
            )

        raise AsbError(message, status_code=status, error_code=error_code, response=data)

    def _request(self, method: str, path: str, **kwargs) -> dict | Any:
        client = self._get_sync_client()
        try:
            resp = client.request(method, path, **kwargs)
        except httpx.RequestError as e:
            raise AsbError(f"Network error: {e}") from e
        self._handle_error(resp)
        if resp.status_code == 204:
            return {}
        try:
            return resp.json()
        except Exception:
            return {"raw": resp.text}

    async def _arequest(self, method: str, path: str, **kwargs) -> dict | Any:
        client = await self._get_async_client()
        try:
            resp = await client.request(method, path, **kwargs)
        except httpx.RequestError as e:
            raise AsbError(f"Network error: {e}") from e
        self._handle_error(resp)
        if resp.status_code == 204:
            return {}
        try:
            return resp.json()
        except Exception:
            return {"raw": resp.text}

    # -----------------------
    # Public API methods (sync)
    # -----------------------

    def health(self) -> dict:
        """GET /v1/health (no auth required)."""
        return self._request("GET", "/v1/health")

    def scrape(
        self,
        url: str,
        method: Literal["GET", "POST"] = "GET",
        headers: dict | None = None,
        data: dict | None = None,
        proxy_provider: str | None = None,
        region: str | None = None,
        fingerprint: str | None = None,
        timeout: int = 30,
        screenshot: bool = False,
        session_id: str | None = None,
        session_type: Literal["stateless", "stateful", "stateful_reset"] = "stateless",
    ) -> dict:
        """
        Execute a browser-backed scrape. See SPEC.md for full semantics.
        """
        payload: dict[str, Any] = {
            "url": url,
            "method": method,
            "timeout": timeout,
            "screenshot": screenshot,
            "session_type": session_type,
        }
        if headers:
            payload["headers"] = headers
        if data is not None:
            payload["data"] = data
        if proxy_provider:
            payload["proxy_provider"] = proxy_provider
        if region:
            payload["region"] = region
        if fingerprint:
            payload["fingerprint"] = fingerprint
        if session_id:
            payload["session_id"] = session_id

        return self._request("POST", "/v1/scrape", json=payload)

    def create_session(
        self,
        region: str = "jp",
        fingerprint: str | None = None,
    ) -> dict:
        """Create a stateful session. Returns session_id, created_at, expires_at."""
        payload: dict[str, Any] = {"region": region}
        if fingerprint:
            payload["fingerprint"] = fingerprint
        return self._request("POST", "/v1/sessions", json=payload)

    def get_session(self, session_id: str) -> dict:
        """Inspect a session (must be owned by the key)."""
        return self._request("GET", f"/v1/sessions/{session_id}")

    def delete_session(self, session_id: str) -> None:
        """Delete a session (must be owned by the key). Returns None on 204."""
        self._request("DELETE", f"/v1/sessions/{session_id}")

    def get_usage(self) -> dict:
        """Get current key usage and limits."""
        return self._request("GET", "/v1/usage")

    def get_billing_portal(self) -> dict:
        """Get Stripe billing portal URL for the authenticated key (if customer exists)."""
        return self._request("GET", "/v1/billing/portal")

    # -----------------------
    # Public API methods (async)
    # -----------------------

    async def health_async(self) -> dict:
        return await self._arequest("GET", "/v1/health")

    async def scrape_async(
        self,
        url: str,
        method: Literal["GET", "POST"] = "GET",
        headers: dict | None = None,
        data: dict | None = None,
        proxy_provider: str | None = None,
        region: str | None = None,
        fingerprint: str | None = None,
        timeout: int = 30,
        screenshot: bool = False,
        session_id: str | None = None,
        session_type: Literal["stateless", "stateful", "stateful_reset"] = "stateless",
    ) -> dict:
        payload: dict[str, Any] = {
            "url": url,
            "method": method,
            "timeout": timeout,
            "screenshot": screenshot,
            "session_type": session_type,
        }
        if headers:
            payload["headers"] = headers
        if data is not None:
            payload["data"] = data
        if proxy_provider:
            payload["proxy_provider"] = proxy_provider
        if region:
            payload["region"] = region
        if fingerprint:
            payload["fingerprint"] = fingerprint
        if session_id:
            payload["session_id"] = session_id

        return await self._arequest("POST", "/v1/scrape", json=payload)

    async def create_session_async(
        self,
        region: str = "jp",
        fingerprint: str | None = None,
    ) -> dict:
        payload: dict[str, Any] = {"region": region}
        if fingerprint:
            payload["fingerprint"] = fingerprint
        return await self._arequest("POST", "/v1/sessions", json=payload)

    async def get_session_async(self, session_id: str) -> dict:
        return await self._arequest("GET", f"/v1/sessions/{session_id}")

    async def delete_session_async(self, session_id: str) -> None:
        await self._arequest("DELETE", f"/v1/sessions/{session_id}")

    async def get_usage_async(self) -> dict:
        return await self._arequest("GET", "/v1/usage")

    async def get_billing_portal_async(self) -> dict:
        return await self._arequest("GET", "/v1/billing/portal")

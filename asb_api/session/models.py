from dataclasses import dataclass, field
from typing import Literal


@dataclass
class ScrapeRequest:
    url: str
    method: Literal["GET", "POST"] = "GET"
    headers: dict = field(default_factory=dict)
    data: dict | None = None
    proxy_provider: str | None = None
    region: str | None = None
    fingerprint: str | None = None
    timeout: int = 30
    screenshot: bool = False
    session_id: str | None = None
    session_type: Literal["stateless", "stateful", "stateful_reset"] = "stateless"


@dataclass
class ScrapeMetadata:
    request_id: str
    provider: str
    region: str | None
    fingerprint_id: str
    worker_id: str
    duration_ms: int
    block_detected: bool
    retries: int


@dataclass
class ScrapeResponse:
    request_id: str
    status: Literal["success", "error", "success_with_retries"]
    html: str | None = None
    screenshot_url: str | None = None
    cookies: dict = field(default_factory=dict)
    headers: dict = field(default_factory=dict)
    metadata: ScrapeMetadata | None = None
    error_code: str | None = None
    message: str | None = None

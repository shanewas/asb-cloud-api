"""ASB Cloud API client exceptions."""


class AsbError(Exception):
    """Base exception for all ASB client errors."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        error_code: str | None = None,
        response: dict | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.response = response or {}

    def __str__(self) -> str:
        if self.error_code:
            return f"[{self.error_code}] {self.message}"
        if self.status_code:
            return f"HTTP {self.status_code}: {self.message}"
        return self.message


class AsbAuthError(AsbError):
    """Authentication failed (missing/invalid API key)."""
    pass


class AsbRateLimitError(AsbError):
    """Rate limit exceeded. Check headers for reset info."""

    def __init__(self, message: str, status_code: int = 429, **kwargs):
        super().__init__(message, status_code=status_code, error_code="RATE_LIMIT_EXCEEDED", **kwargs)
        self.limit = kwargs.get("limit")
        self.remaining = kwargs.get("remaining")
        self.reset_at = kwargs.get("reset_at")


class AsbOverageError(AsbError):
    """Usage overage limit exceeded (402)."""

    def __init__(self, message: str, status_code: int = 402, **kwargs):
        super().__init__(message, status_code=status_code, error_code="OVERAGE_LIMIT_EXCEEDED", **kwargs)
        self.overage_cost_usd = kwargs.get("overage_cost_usd")


class AsbNotFoundError(AsbError):
    """Resource not found (e.g. session expired or not owned)."""
    pass

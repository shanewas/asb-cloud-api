"""
Standardized API error handling.

All public API errors should return a consistent shape:

    {"error_code": "SOME_CODE", "message": "Human readable explanation."}

This aligns with SPEC.md section 8 and the error examples in section 7.

Rate limit responses additionally include headers (X-RateLimit-*) and may include
extra fields in the body.
"""

from fastapi import HTTPException as FastAPIHTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.status import HTTP_400_BAD_REQUEST


# Mapping from common raw detail strings (or substrings) to canonical codes
# Used as a fallback when code is not explicitly provided.
_STRING_TO_CODE = {
    "missing authorization": "MISSING_AUTH",
    "invalid api key": "INVALID_API_KEY",
    "service not initialized": "SERVICE_NOT_INITIALIZED",
    "session store not initialized": "SERVICE_NOT_INITIALIZED",
    "session not found": "SESSION_NOT_FOUND",
    "usage tracker not initialized": "SERVICE_NOT_INITIALIZED",
    "rate limit": "RATE_LIMIT_EXCEEDED",
    "overage": "OVERAGE_LIMIT_EXCEEDED",
}


class APIError(FastAPIHTTPException):
    """
    Use this (or raise_structured_error) for all new error cases so the
    response shape is consistent.
    """

    def __init__(
        self,
        status_code: int,
        error_code: str,
        message: str,
        headers: dict[str, str] | None = None,
        **extra,
    ):
        body = {"error_code": error_code, "message": message, **extra}
        super().__init__(status_code=status_code, detail=body, headers=headers)


def raise_structured_error(
    status_code: int,
    error_code: str,
    message: str,
    headers: dict[str, str] | None = None,
    **extra,
):
    """Convenience function if you prefer not to import the exception class."""
    raise APIError(
        status_code=status_code,
        error_code=error_code,
        message=message,
        headers=headers,
        **extra,
    )


async def structured_http_exception_handler(request: Request, exc: StarletteHTTPException | FastAPIHTTPException):
    """
    Converts any HTTPException into our standard error shape.

    - If the detail is already a dict containing "error_code", we use it as-is
      (allows RateLimitExceeded etc. to include extra fields like limit/remaining).
    - Otherwise we try to map the string detail to a known code and produce
      {"error_code": "...", "message": "..."}.
    - Rate limit headers are preserved.
    """
    headers = getattr(exc, "headers", None) or {}

    if isinstance(exc.detail, dict):
        # Already structured (e.g. from RateLimitExceeded or OverageLimitExceeded)
        content = dict(exc.detail)  # copy
        # Ensure the two required fields exist
        if "error_code" not in content:
            content["error_code"] = "INTERNAL_ERROR"
        if "message" not in content:
            content["message"] = str(content)
    else:
        # Plain string or other -> normalize
        detail_str = str(exc.detail or "Internal error")
        # Try to find a good code
        lowered = detail_str.lower()
        error_code = "INTERNAL_ERROR"
        for key, code in _STRING_TO_CODE.items():
            if key in lowered:
                error_code = code
                break

        # Special case: 404 from FastAPI for unknown routes etc. -> treat as bad request?
        if exc.status_code == 404 and error_code == "INTERNAL_ERROR":
            error_code = "BAD_REQUEST"
            detail_str = detail_str or "Resource not found"

        content = {"error_code": error_code, "message": detail_str}

    return JSONResponse(
        status_code=exc.status_code,
        content=content,
        headers=headers,
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Turn Pydantic/FastAPI validation errors (usually 422) into BAD_REQUEST
    with our standard shape.
    """
    # Take the first error message for simplicity
    try:
        first = exc.errors()[0]
        loc = ".".join(str(x) for x in first.get("loc", []))
        msg = first.get("msg", "Invalid request")
        message = f"{loc}: {msg}" if loc else msg
    except Exception:
        message = "Invalid request payload"

    return JSONResponse(
        status_code=HTTP_400_BAD_REQUEST,
        content={
            "error_code": "BAD_REQUEST",
            "message": message,
        },
    )


def install_error_handlers(app):
    """Call this once after creating the FastAPI app."""
    # Register for both Starlette and FastAPI HTTPException (FastAPI registers its own)
    app.add_exception_handler(StarletteHTTPException, structured_http_exception_handler)
    app.add_exception_handler(FastAPIHTTPException, structured_http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

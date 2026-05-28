# ASB Cloud API — Phase 0: Prototype

**Goal:** Prove the core loop works. Single VPS, curl-accessible. No auth, no rate-limit, no persistence.
**Working directory:** `/root/asb-cloud-api`

## Context

Read the full spec at `SPEC.md` before starting. The architecture is modular:
- Proxy providers are plugins behind a common interface
- Everything is async (asyncio)
- Workers run ASB (Agentic Stealth Browser) with Playwright
- No Decodo-specific code anywhere — proxy is an abstraction

## What to Build

### 1. Project skeleton

Create `requirements.txt` with these minimum dependencies:
```
fastapi>=0.115.0
uvicorn[standard]>=0.34.0
pydantic>=2.10.0
pydantic-settings>=2.7.0
playwright>=1.50.0
python-dotenv>=1.0.0
pyyaml>=6.0.2
cryptography>=44.0.0
```

### 2. Config system (`asb_api/config.py`)

Load `config.yaml` with these top-level keys (from SPEC.md Section 4):
- `app` — host, port, log_level, debug
- `providers` — dict of provider configs (null, custom, decodo, brightdata)
- `provider_priority` — primary, fallback
- `pool` — max_workers, session_ttl_seconds, idle_timeout_seconds, prewarm_on_startup, max_retries_per_request
- `fingerprint` — rotation_strategy, default_preset, presets dict
- `rate_limits` — free/starter/pro/enterprise limits
- `self_hosted` — enabled, license_key_required, telemetry_enabled

Environment variable substitution: `${VAR_NAME}` in YAML values → `os.environ.get("VAR_NAME")`.

### 3. Proxy Provider Interface (`asb_api/providers/`)

Create this exact structure:

**`asb_api/providers/__init__.py`**
```python
from .base import ProxyProviderInterface, ProxyConfig, PoolExhaustedError, ProviderError
from .null import NullProvider
from .custom import CustomProvider
```
Export a `ProviderRegistry` class with:
- `register(name, cls)` — register a provider class
- `get(name)` → provider instance (singleton per name)
- `list_providers()` → list of available provider names
- `initialize_from_config(config: dict)` — auto-register all providers defined in config.yaml

**`asb_api/providers/base.py`**
```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

class PoolExhaustedError(Exception): pass
class ProviderError(Exception): pass

@dataclass
class ProxyConfig:
    host: str
    port: int
    username: str | None = None
    password: str | None = None
    protocol: Literal["http", "socks5", "socks4"] = "http"
    region: str | None = None
    sticky: bool = False
    session_token: str | None = None

class ProxyProviderInterface(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def get_proxy(self, region: str | None = None) -> ProxyConfig:
        """Borrow a proxy from the pool. Raises PoolExhaustedError if empty."""
        ...

    @abstractmethod
    async def release_proxy(self, proxy: ProxyConfig) -> None:
        """Return proxy to the pool after use."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Returns True if the provider is reachable."""
        ...
```

**`asb_api/providers/null.py`**
```python
# NullProvider: direct connection, no proxy
# get_proxy() returns ProxyConfig with host="DIRECT", port=0
# Always healthy, never exhausts
```

**`asb_api/providers/custom.py`**
```python
# CustomProvider: user-defined static proxy list from config.yaml
# Loads list from config:
#   custom:
#     proxies:
#       - host: "203.0.113.1"
#         port: 8080
#         username: ""
#         password: ""
# Round-robin allocation. Returns proxy to front of queue on release.
```

### 4. Fingerprint module (`asb_api/fingerprint/`)

**`asb_api/fingerprint/generator.py`**
```python
@dataclass
class Fingerprint:
    user_agent: str
    viewport: tuple[int, int]
    webgl_vendor: str
    canvas: Literal["noise", "empty", "off"]
    accept_language: str | None = None
    platform: str | None = None

class FingerprintGenerator:
    def __init__(self, presets: dict):
        self.presets = presets  # from config.yaml

    def get(self, preset_name: str) -> Fingerprint:
        """Return a Fingerprint for the named preset."""
        ...

    def rotate(self, current: Fingerprint) -> Fingerprint:
        """Return a new Fingerprint with a variant of the same preset."""
        ...
```

Load presets from `config.yaml` fingerprint.presets section.

### 5. Session models (`asb_api/session/models.py`)

```python
from dataclasses import dataclass
from datetime import datetime
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
```

### 6. Worker (`asb_api/workers/worker.py`)

```python
import asyncio
import uuid
import time
from .asb_runner import ASBRunner

class ASBWorker:
    def __init__(
        self,
        worker_id: str,
        provider: ProxyProviderInterface,
        fingerprint_generator: FingerprintGenerator,
    ):
        self.worker_id = worker_id
        self.provider = provider
        self.fingerprint_generator = fingerprint_generator
        self.runner: ASBRunner | None = None

    async def start(self):
        self.runner = ASBRunner()

    async def stop(self):
        if self.runner:
            await self.runner.close()

    async def scrape(self, request: ScrapeRequest) -> ScrapeResponse:
        request_id = f"req_{uuid.uuid4().hex[:12]}"
        start = time.monotonic()

        # Get proxy from provider
        proxy = None
        if self.provider.name != "null":
            proxy = await self.provider.get_proxy(request.region)

        # Get fingerprint
        fp = self.fingerprint_generator.get(
            request.fingerprint or "general"
        )

        try:
            # Run ASB
            result = await self.runner.run(
                url=request.url,
                method=request.method,
                headers=request.headers,
                data=request.data,
                proxy=proxy,
                fingerprint=fp,
                timeout=request.timeout,
                screenshot=request.screenshot,
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            return ScrapeResponse(
                request_id=request_id,
                status="success",
                html=result["html"],
                screenshot_url=result.get("screenshot_url"),
                cookies=result.get("cookies", {}),
                headers=result.get("headers", {}),
                metadata=ScrapeMetadata(
                    request_id=request_id,
                    provider=self.provider.name,
                    region=request.region,
                    fingerprint_id=fp.user_agent[:50],
                    worker_id=self.worker_id,
                    duration_ms=duration_ms,
                    block_detected=result.get("block_detected", False),
                    retries=0,
                ),
            )
        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            return ScrapeResponse(
                request_id=request_id,
                status="error",
                error_code="WORKER_ERROR",
                message=str(e),
                metadata=ScrapeMetadata(
                    request_id=request_id,
                    provider=self.provider.name,
                    region=request.region,
                    fingerprint_id="",
                    worker_id=self.worker_id,
                    duration_ms=duration_ms,
                    block_detected=False,
                    retries=0,
                ),
            )
        finally:
            if proxy:
                await self.provider.release_proxy(proxy)
```

### 7. ASB Runner (`asb_api/workers/asb_runner.py`)

A placeholder that uses `playwright` directly. Since ASB wraps Playwright, we use Playwright's async API here for the prototype. The ASB integration comes in Phase 1.

```python
from playwright.async_api import async_playwright, Page
import asyncio

class ASBRunner:
    def __init__(self):
        self.playwright = None
        self.browser = None

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,
        )
        return self

    async def __aexit__(self, *args):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    async def close(self):
        await self.__aexit__()

    async def run(self, url, method, headers, data, proxy, fingerprint, timeout, screenshot):
        context = await self.browser.new_context(
            user_agent=fingerprint.user_agent,
            viewport={"width": fingerprint.viewport[0], "height": fingerprint.viewport[1]},
            proxy={
                "server": f"http://{proxy.host}:{proxy.port}",
                "username": proxy.username,
                "password": proxy.password,
            } if proxy and proxy.host != "DIRECT" else None,
            extra_http_headers=headers or {},
        )
        page = await context.new_page()

        try:
            if method == "POST":
                response = await page.goto(url, method="post", data=data or {}, timeout=timeout * 1000)
            else:
                response = await page.goto(url, timeout=timeout * 1000)

            html = await page.content()
            cookies = await context.cookies()
            headers_out = dict(response.headers) if response else {}

            screenshot_url = None
            if screenshot:
                screenshot_url = f"/tmp/screenshots/{uuid.uuid4().hex}.png"
                await page.screenshot(path=screenshot_url)

            return {
                "html": html,
                "cookies": {c["name"]: c["value"] for c in cookies},
                "headers": headers_out,
                "screenshot_url": screenshot_url,
                "block_detected": False,
            }
        finally:
            await context.close()
```

**Note:** The proxy setup in `new_context` only works for http/socks proxies — the null/DIRECT case needs `proxy=None`.

### 8. Worker Pool (`asb_api/workers/pool.py`)

```python
import asyncio

class WorkerPool:
    def __init__(
        self,
        size: int,
        provider: ProxyProviderInterface,
        fingerprint_generator: FingerprintGenerator,
    ):
        self.size = size
        self.semaphore = asyncio.Semaphore(size)
        self.workers = [
            ASBWorker(f"worker-{i}", provider, fingerprint_generator)
            for i in range(size)
        ]

    async def start_all(self):
        for w in self.workers:
            await w.start()

    async def stop_all(self):
        for w in self.workers:
            await w.stop()

    async def acquire(self) -> ASBWorker:
        await self.semaphore.acquire()
        # Find first idle worker
        for w in self.workers:
            if not hasattr(w, "_busy"):
                w._busy = True
                return w
        return self.workers[0]

    def release(self, worker: ASBWorker):
        worker._busy = False
        self.semaphore.release()
```

### 9. API Server (`asb_api/api/routes/scrape.py`)

```python
from fastapi import APIRouter, HTTPException
from asb_api.session.models import ScrapeRequest, ScrapeResponse
from asb_api.providers import ProviderRegistry
from asb_api.workers.pool import WorkerPool

router = APIRouter()
pool: WorkerPool | None = None

def set_pool(p: WorkerPool):
    global pool
    pool = p

@router.post("/v1/scrape", response_model=ScrapeResponse)
async def scrape(request: ScrapeRequest):
    if not pool:
        raise HTTPException(status_code=503, detail="Service not initialized")

    worker = await pool.acquire()
    try:
        result = await worker.scrape(request)
        return result
    finally:
        pool.release(worker)

@router.get("/v1/health")
async def health():
    return {"status": "ok", "pool_size": pool.size if pool else 0}
```

### 10. App entry point (`asb_api/__main__.py`)

```python
import asyncio
import logging
from fastapi import FastAPI
from asb_api.config import load_config
from asb_api.providers import ProviderRegistry
from asb_api.fingerprint.generator import FingerprintGenerator
from asb_api.workers.pool import WorkerPool
from asb_api.api.routes.scrape import router as scrape_router, set_pool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="ASB Cloud API")
app.include_router(scrape_router)

@app.on_event("startup")
async def startup():
    config = load_config()

    # Initialize providers
    registry = ProviderRegistry()
    registry.initialize_from_config(config.get("providers", {}))
    active = config.get("provider_priority", {}).get("primary", "null")
    provider = registry.get(active)

    # Initialize fingerprint generator
    fp_gen = FingerprintGenerator(config.get("fingerprint", {}).get("presets", {}))

    # Initialize worker pool
    pool = WorkerPool(
        size=config.get("pool", {}).get("max_workers", 5),
        provider=provider,
        fingerprint_generator=fp_gen,
    )
    await pool.start_all()
    set_pool(pool)

    logger.info(f"ASB Cloud API started with provider={active}, workers={pool.size}")

@app.on_event("shutdown")
async def shutdown():
    logger.info("Shutting down ASB Cloud API...")

if __name__ == "__main__":
    import uvicorn
    cfg = load_config()
    uvicorn.run(
        "asb_api.__main__:app",
        host=cfg.get("app", {}).get("host", "0.0.0.0"),
        port=cfg.get("app", {}).get("port", 8000),
        reload=False,
    )
```

### 11. `__init__.py` files

Create empty `__init__.py` in every package:
- `asb_api/__init__.py`
- `asb_api/api/__init__.py`
- `asb_api/api/routes/__init__.py`
- `asb_api/providers/__init__.py`
- `asb_api/fingerprint/__init__.py`
- `asb_api/session/__init__.py`
- `asb_api/workers/__init__.py`

### 12. Initial `config.yaml`

```yaml
app:
  host: "0.0.0.0"
  port: 8000
  log_level: "info"
  debug: false

security:
  api_key_hash_algorithm: "argon2"
  log_url_domains_only: true
  redact_authorization_headers: true

providers:
  null:
    enabled: true
  custom:
    enabled: false
    proxies: []
  decodo:
    enabled: false
    api_key: ""
    pool_size: 10
    regions: [jp, us, eu]
    default_region: jp
  brightdata:
    enabled: false
    api_key: ""

provider_priority:
  primary: null
  fallback: null

pool:
  max_workers: 5
  session_ttl_seconds: 300
  idle_timeout_seconds: 60
  prewarm_on_startup: true
  max_retries_per_request: 2

fingerprint:
  rotation_strategy: "per_request"
  default_preset: "general"
  presets:
    general:
      viewport: [1920, 1080]
      user_agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
      webgl_vendor: "Google Inc. (Intel)"
      canvas: "noise"
      accept_language: "en-US,en;q=0.9"
    japan_ecommerce:
      viewport: [1920, 1080]
      user_agent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
      webgl_vendor: "Apple Inc."
      canvas: "noise"
      accept_language: "ja-JP,ja;q=0.9"
    mobile_jp:
      viewport: [390, 844]
      user_agent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
      platform: "iPhone"
      canvas: "noise"
      accept_language: "ja-JP,ja;q=0.9"

rate_limits:
  free:
    requests: 500
    window_seconds: 3600
    concurrent_sessions: 2
  starter:
    requests: 25000
    window_seconds: 86400
    concurrent_sessions: 10
  pro:
    requests: 200000
    window_seconds: 86400
    concurrent_sessions: 50

self_hosted:
  enabled: true
  license_key_required: false
  telemetry_enabled: false
```

## Success Criteria

After building:
1. `python -m asb_api` starts the server on port 8000 without errors
2. `curl -X POST http://localhost:8000/v1/scrape -H "Content-Type: application/json" -d '{"url":"https://example.com"}'` returns HTML
3. `curl http://localhost:8000/v1/health` returns `{"status": "ok"}`
4. All providers (`null`, `custom`) can be switched by changing `provider_priority.primary` in config.yaml

## Dont
- Do NOT install Playwright browsers yet (that comes later with ASB)
- Do NOT commit to git or push
- Do NOT create billing/Stripe code
- Do NOT create dashboard code
- Do NOT run the server — just verify it starts without import errors
- Do NOT use asyncpg or Redis (those come in later phases)

## Verification

After completing the build, run:
```bash
cd /root/asb-cloud-api
python -c "
from asb_api.config import load_config
from asb_api.providers import ProviderRegistry
from asb_api.fingerprint.generator import FingerprintGenerator
from asb_api.session.models import ScrapeRequest, ScrapeResponse
print('All imports OK')
cfg = load_config()
print(f'Config loaded: app.port={cfg[\"app\"][\"port\"]}')
registry = ProviderRegistry()
registry.initialize_from_config(cfg.get('providers', {}))
print(f'Providers: {registry.list_providers()}')
fp = FingerprintGenerator(cfg.get('fingerprint', {}).get('presets', {}))
print(f'Fingerprints: {list(cfg.get(\"fingerprint\", {}).get(\"presets\", {}).keys())}')
"
```
If this prints OK and exits 0, Phase 0 is complete.

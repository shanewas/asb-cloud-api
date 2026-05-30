# Redis Rate Limiting Evaluation

Status: Decision Document (post-v1)
Issue: [#12](https://github.com/shanewas/asb-cloud-api/issues/12)
Last updated: 2026-05-30

## 1. Purpose

This document evaluates whether and when to introduce Redis-backed rate limiting for ASB Cloud API. The current implementation supports two backends — an in-memory sliding window (`asb_api/api/rate_limiter.py`) and a PostgreSQL advisory-lock-based limiter (`asb_api/db/rate_limiter.py`) — both with identical `check()` contracts and HTTP response shapes. Redis rate limiting is explicitly deferred from v1 per `SPEC.md` §2 (Out of Scope).

## 2. Current Architecture

### 2.1 In-Memory (`SlidingWindowLimiter`)

- Stores per-key `deque[float]` of request timestamps in process memory.
- Protected by a single `asyncio.Lock` — serializes all concurrent checks.
- On each request: evicts stale timestamps (`O(n)` where `n` is the window count), appends current time, returns `(allowed, remaining, reset_at)`.
- **Scale limit**: Single-process only. Restart or horizontal scale loses all window state. Two API processes behind a load balancer have independent windows — a key can submit 2x their tier limit.

### 2.2 PostgreSQL (`PostgresRateLimiter`)

- Acquires a per-key advisory lock (`pg_try_advisory_lock(key_hash)`) to serialize checks.
- Counts rows in `usage_records` where `created_at > cutoff` (windowed COUNT query).
- If COUNT >= max_requests, finds the oldest row to compute precise `reset_at`.
- Returns `(allowed, remaining, reset_at)` using the same `RateLimitExceeded` contract.
- **Scale characteristics**:
  - Correct across multiple API processes (single PG, advisory lock serializes).
  - COUNT query hits `usage_records.created_at` index. At 1M+ rows per day, COUNT scans a growing index range.
  - Advisory lock contention: every request for the same key serializes. At high throughput (~500+ req/s per key), lock wait time becomes a bottleneck (current implementation waits 50ms, retries once, then denies).
  - No TTL mechanism: stale `usage_records` must be pruned by external cron/rollup to keep the window scan efficient.

## 3. Scale Threshold Analysis

| Metric | In-Memory Viable | PostgreSQL Viable | Redis Warranted |
|--------|-----------------|-------------------|-----------------|
| API processes | 1 | 1-3 | 3+ |
| Requests/sec (single key) | < 200 | < 100 | 100+ |
| Requests/sec (total) | < 500 | < 2000 | 2000+ |
| Usage records/day | N/A | < 10M | 10M+ |
| Latency budget for rate check | < 1ms | < 10ms | < 1ms |
| Multi-region deployment | No | No | Yes |

**Recommended trigger**: Introduce Redis rate limiting when **any** of these conditions are met:

1. More than **one API process** is deployed behind a load balancer and rate limit correctness is required.
2. Peak throughput exceeds **100 requests/second per API key**.
3. The PostgreSQL `usage_records` table exceeds **10M rows/day**, causing COUNT queries to take >10ms.
4. A **multi-region** deployment is needed where advisory locks on a single PostgreSQL instance become a single point of failure.

## 4. Algorithm Comparison

### 4.1 Sliding Window Log (current approach)

Each request timestamp is stored individually. On check, evict stale entries, count remaining, compare to limit.

- **Accuracy**: Exact count within the window.
- **Memory**: `O(n)` — stores every request timestamp for the window duration. For a pro-tier key (200K/day), the deque holds up to 200K `float` values (~1.6 MB per key in memory). In Redis, this is ~200K list/sorted-set entries.
- **Correctness**: Strongest — no approximation. The exact count at every millisecond is known.
- **Drawback**: High memory/Redis usage for high-volume tiers. Every check writes one entry and reads the window.

### 4.2 Fixed Window

Count requests in fixed buckets (e.g., per-minute). Reset the counter at bucket boundaries.

- **Accuracy**: Burst at bucket boundaries can double actual throughput.
- **Memory**: `O(1)` — single counter per key per bucket.
- **Implementation**: `INCR key:2026-05-30T14:35` with `EXPIRE` set to window duration.
- **Drawback**: Unacceptable for production billing. A user can submit 500 requests at 14:59:59 and another 500 at 15:00:00, consuming 1000 requests in 2 seconds on a 500/hour limit.

**Verdict**: Not suitable for this use case. Billing-gated tiers require better accuracy.

### 4.3 Sliding Window Counter (approximate)

Store a counter per sub-window (e.g., per-second or per-minute buckets within the larger window). On check, sum counters across active sub-windows and compare to limit.

- **Accuracy**: Approximation — within ±1 sub-window worth of requests. For 1-second sub-windows, accuracy is excellent.
- **Memory**: `O(window_seconds / sub_window_seconds)`. For a 1-hour window with 1-second sub-windows: 3600 counters (~28 KB per key). For a 1-day window with 1-minute sub-windows: 1440 counters (~11 KB per key).
- **Implementation in Redis**:
  ```
  # Each sub-window is a key: ratelimit:{key_id}:{bucket_ts}
  # bucket_ts = floor(now / sub_window_seconds) * sub_window_seconds
  INCR ratelimit:{key_id}:{bucket_ts}
  EXPIRE ratelimit:{key_id}:{bucket_ts} {window_seconds}
  
  # To check: SUM keys in range [now - window_seconds, now]
  EVAL script that iterates buckets and sums.
  ```
- **Verdict**: Best balance of accuracy, memory, and performance for this use case.

### 4.4 Token Bucket

Maintain a token count that refills at a fixed rate. Each request consumes one token. If bucket is empty, deny.

- **Accuracy**: Allows bursts up to bucket capacity. Refill rate = limit / window. For 500 req/hour, refill rate = 0.139 tokens/sec.
- **Memory**: `O(1)` — single counter + last refill timestamp.
- **Behavior**: A user could burst 500 tokens, wait 1 hour to refill, burst again. This does NOT match the current "N requests per window" semantics in the tier config.
- **Verdict**: Semantically incompatible with the current tier model. Would require redefining tier limits as "N requests per window with burst allowance of B." Not recommended for v1 compatibility.

### 4.5 Generic Cell Rate Algorithm (GCRA)

A leaky-bucket variant commonly used in Redis rate-limiters. Stores a single "theoretical arrival time" (TAT) per key.

- **Accuracy**: Exact sliding window behavior with `O(1)` state.
- **Memory**: `O(1)` — one float per key.
- **Implementation**: Single Redis key storing TAT. `EVAL` script computes: if `now >= tat - period + burst`, allowed, update TAT. Else, denied.
- **Drawback**: "Burst" parameter is a single value, not a count-over-window. The current tier model is count-based, not rate-based, so GCRA does not map cleanly to "500 requests per 3600 seconds."

**Verdict**: Elegant but semantically mismatched. Would require redefining all tier limits as rate + burst.

### 4.6 Recommendation: Sliding Window Counter

| Criteria | Sliding Window Log (current) | Fixed Window | Sliding Window Counter | Token Bucket | GCRA |
|----------|-----------------------------|-------------|------------------------|-------------|------|
| Matches current tier semantics | Yes | No | Near-exact | No | No |
| Memory per key | High (O(n)) | O(1) | Moderate O(w/s) | O(1) | O(1) |
| Redis operations per check | O(n) read | O(1) incr | O(w/s) sum | O(1) incr + get | O(1) get + set |
| Burst fairness at boundaries | Perfect | Poor | ±1 sub-window | Allows full burst | Rate-based |
| Multi-process safe | Yes (with lock) | Yes | Yes | Yes | Yes |

**Recommended algorithm**: Sliding Window Counter with configurable sub-window granularity:

- **free tier** (1h window): 1-second sub-windows → 3600 counters.
- **starter/pro tiers** (24h window): 1-minute sub-windows → 1440 counters.
- **enterprise tier**: Skip entirely (unlimited).

This preserves the exact "N requests per W seconds" contract within one sub-window of precision while keeping Redis memory and operation count bounded at `O(w/s)` independent of actual request volume.

## 5. Redis Data Model

### 5.1 Key Schema

```
ratelimit:{key_id}:{bucket_ts}
```

- `key_id`: The API key identifier (the SHA-256 hash, not the raw key).
- `bucket_ts`: Unix timestamp floored to the sub-window boundary (e.g., `floor(now / 60) * 60` for 1-minute sub-windows).
- Value: Integer counter for requests in that sub-window.
- TTL: Set to `window_seconds` on first `INCR`. Redis auto-evicts stale buckets.

### 5.2 Lua Check Script (atomic)

```lua
-- KEYS[1..N]: bucket keys for each sub-window in range
-- ARGV[1]: max_requests
-- ARGV[2]: window_seconds
-- ARGV[3]: current_bucket_ts (the bucket for this request)
-- ARGV[4]: key TTL

local total = 0
for i = 1, #KEYS do
    total = total + (redis.call('GET', KEYS[i]) or 0)
end

if total >= tonumber(ARGV[1]) then
    -- Calculate reset_at: the oldest bucket's window end
    -- For denied requests, return the earliest reset time
    local earliest_reset = tonumber(ARGV[3]) + tonumber(ARGV[2])
    for i = 1, #KEYS do
        local exists = redis.call('EXISTS', KEYS[i])
        if exists == 1 then
            local bucket_start = tonumber(string.sub(KEYS[i], -10))  -- hacky; better to pass
            earliest_reset = math.min(earliest_reset, bucket_start + tonumber(ARGV[2]))
        end
    end
    return {0, total, earliest_reset}  -- denied
end

-- Increment current bucket
redis.call('INCR', KEYS[#KEYS])
redis.call('EXPIRE', KEYS[#KEYS], tonumber(ARGV[4]))

local remaining = tonumber(ARGV[1]) - total - 1
return {1, remaining, tonumber(ARGV[3]) + tonumber(ARGV[2])}  -- allowed
```

### 5.3 Alternative: Sorted Set Approach

Instead of multiple keys, use a single sorted set per `key_id`:

```
ZADD ratelimit:{key_id} {now} {request_id}
EXPIRE ratelimit:{key_id} {window_seconds}

-- On check:
ZREMRANGEBYSCORE ratelimit:{key_id} 0 {now - window_seconds}
count = ZCARD ratelimit:{key_id}
```

- **Pros**: Exact sliding window log, simpler key management.
- **Cons**: `O(log n)` per operation, memory proportional to request count (200K entries for pro tier at peak). For high-volume tiers, sorted-set memory blows up.
- **Verdict**: Use for low-volume tiers (free) where exact counting matters; use counter approach for high-volume tiers (starter, pro).

### 5.4 Hybrid Strategy (Recommended)

| Tier | Strategy | Rationale |
|------|----------|-----------|
| free | Sorted set (exact log) | Low volume (500/hr). Exactness matters for free-tier fairness. |
| starter | Sliding window counter (1-min buckets) | Medium volume (25K/day). Approximation acceptable. |
| pro | Sliding window counter (1-min buckets) | High volume (200K/day). Approximation with bounded memory. |
| enterprise | Skip (return unlimited) | No rate limit needed. |

### 5.5 Redis Connection

- Use `redis-py` with `ConnectionPool` (async via `aioredis` / `redis.asyncio`).
- Single Redis endpoint sufficient for <10K req/s. Redis Cluster for >10K req/s.
- Configure via environment variables:
  ```bash
  REDIS_URL=redis://localhost:6379/0
  REDIS_RATE_LIMIT_PREFIX=ratelimit  # optional namespace
  ```

## 6. Preserving Existing Contract

### 6.1 Error Shape (must NOT change)

```json
{
  "error_code": "RATE_LIMIT_EXCEEDED",
  "message": "Rate limit reached",
  "limit": 500,
  "remaining": 0,
  "reset_at": 1716893100
}
```

### 6.2 Response Headers (must NOT change)

```
X-RateLimit-Limit: 500
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1716893100
```

### 6.3 API Contract (must NOT change)

- `check(key_id, tier) -> (bool, int, int)` — same signature as `SlidingWindowLimiter` and `PostgresRateLimiter`.
- Raises `RateLimitExceeded` on denial.
- Raises `OverageLimitExceeded` on monthly overage (same as PG limiter, if usage tracker attached).

### 6.4 Implementation Strategy

The existing `SlidingWindowLimiter`, `PostgresRateLimiter`, and the new `RedisRateLimiter` should all implement a common interface (not currently formalized, but implicitly:

```python
class RateLimiterInterface:
    async def check(self, key_id: str, tier: str = "free") -> tuple[bool, int, int]: ...
```

A `RedisRateLimiter` class in `asb_api/db/rate_limiter.py` (or a separate `asb_api/db/redis_rate_limiter.py`) would implement this same contract, making it a drop-in replacement at startup:

```python
# In __main__.py:
if REDIS_URL:
    from asb_api.db.redis_rate_limiter import RedisRateLimiter
    limiter = RedisRateLimiter(limits_by_tier=limits_cfg, redis_url=REDIS_URL)
elif DATABASE_URL:
    from asb_api.db.rate_limiter import PostgresRateLimiter
    limiter = PostgresRateLimiter(limits_by_tier=limits_cfg, usage_tracker=ut)
else:
    from asb_api.api.rate_limiter import SlidingWindowLimiter
    limiter = SlidingWindowLimiter(limits_by_tier=limits_cfg)
```

The scrape route (`asb_api/api/routes/scrape.py`, line 54-55) calls `await rate_limiter.check(key_id, tier)` regardless of which backend is active. No route-level changes needed.

## 7. Migration Plan

### Phase 1: Soft Launch (post-v1)

1. Add `redis` to `pyproject.toml` optional dependencies: `pip install asb-cloud-api[redis]`.
2. Implement `RedisRateLimiter` with the sliding window counter algorithm.
3. Add `REDIS_URL` environment variable support to `__main__.py` startup.
4. Add `redis` to `config.yaml` under a new `redis:` block.
5. Deploy with `--feature-flag redis-rate-limit` or `REDIS_URL` set. Fall back to PostgreSQL limiter if Redis is unreachable.
6. Run both limiters in shadow mode (check Redis, but enforce PostgreSQL) for one release cycle.

### Phase 2: Cutover

1. Switch enforcement to Redis (`REDIS_URL` set = Redis is authoritative).
2. PostgreSQL limiter becomes fallback only if Redis is unreachable.
3. Monitor: compare Redis counts vs PostgreSQL `usage_records` counts for drift.

### Phase 3: Cleanup (future major version)

1. Remove PostgreSQL advisory-lock rate limiter.
2. Keep PostgreSQL `usage_records` for analytics/rollups only.
3. Optionally remove in-memory limiter (or keep for local dev without Redis).

### Backward Compatibility

- Operators who do not set `REDIS_URL` continue using the existing PostgreSQL or in-memory limiter.
- No configuration changes required for existing deployments.
- Tier semantics, error contract, and response headers are unchanged.

## 8. Concurrency Testing Plan

When Redis rate limiting is implemented, the following tests are required:

1. **Single-key burst test**: 100 concurrent requests from the same API key against a limit of 50. Assert exactly 50 succeed and 50 get 429.
2. **Multi-key isolation test**: 3 keys, each with limit 10, 30 concurrent requests per key. Assert each key independently gets 10 successes.
3. **Multi-process test**: 3 API processes behind a load balancer, single Redis. Same key submits 100 concurrent requests against a limit of 50. Assert exactly 50 succeed.
4. **Window boundary test**: Submit requests just before and just after a sub-window boundary. Verify the count is correct (±1 sub-window tolerance).
5. **Redis failure test**: Redis unreachable → fallback to PostgreSQL or deny-open with 503.
6. **Header contract test**: All 429 responses include `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` with correct values.
7. **Error contract test**: Response body includes `error_code: "RATE_LIMIT_EXCEEDED"` with `limit`, `remaining`, `reset_at`.
8. **Overage integration test**: Paid tier with overage config, Redis limiter with attached usage tracker. Verify `OverageLimitExceeded` (402) is raised before window check.

## 9. Decision

| Date | Decision | Owner |
|------|----------|-------|
| 2026-05-30 | Redis rate limiting is deferred to post-v1. Implementation is warranted when: >1 API process, >100 req/s per key, >10M usage records/day, or multi-region deployment. | Maintainer |
| 2026-05-30 | Recommended algorithm: Sliding Window Counter with 1-second sub-windows for free tier, 1-minute for paid tiers. Sorted-set exact log for free tier. | Maintainer |
| 2026-05-30 | Existing error contract, response headers, and API signature must be preserved. Implementation uses the same `check()` interface as the current limiters. | Maintainer |
| 2026-05-30 | Migration path: shadow mode (1 release), cutover (1 release), cleanup (future major). Backward compatible — no changes for non-Redis deployments. | Maintainer |

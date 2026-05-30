# Customer Dashboard Architecture Proposal

Status: Architecture Proposal (post-v1)
Issue: [#9](https://github.com/shanewas/asb-cloud-api/issues/9)
Last updated: 2026-05-30

## 1. Purpose

This document proposes the architecture for a self-serve customer dashboard for ASB Cloud API. The dashboard is explicitly deferred from v1 per `SPEC.md` §2 (Out of Scope). This proposal establishes the design before implementation begins.

## 2. Architecture Principles

1. **Decoupled frontend**: The dashboard is a separate deployable (static SPA or standalone service), not embedded in the API server. The API remains the single source of truth for all data.
2. **API-first**: All dashboard features are backed by REST API endpoints (new or existing) on the ASB Cloud API server. No direct database access from the dashboard frontend.
3. **Same auth model**: Dashboard login issues the same API key type (`sk_live_*` / `sk_test_*`). The dashboard is an authenticated API client the same as any SDK.
4. **Progressive enhancement**: Start with read-only views (usage, sessions, invoices), add write operations (key CRUD, plan changes) in later phases.
5. **Self-hosted compatible**: Self-hosted operators should be able to deploy the dashboard alongside the API with minimal configuration.

## 3. Technology Stack (Recommendation)

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Frontend | React + TypeScript | Industry standard, wide ecosystem, good DX for data-heavy dashboards |
| Routing | React Router v7 | Client-side routing for SPA, simple API |
| Charts | Recharts | Lightweight, React-native, sufficient for usage/stat charts |
| API Client | fetch / ky | Lightweight; no heavy GraphQL client needed for REST |
| Styling | Tailwind CSS v4 | Utility-first, rapid prototyping, small bundle |
| Build | Vite | Fast HMR, ESBuild bundling, TypeScript out of the box |
| Auth | Bearer token (API key) | Same `Authorization: Bearer sk_live_*` mechanism |
| State | React Context + SWR | Server-state caching via SWR, app state via Context |
| Self-hosted | Nginx + SPA static files | Single-page app served from any static host or Docker |

### Rationale for Separating from the API Server

- **Independent deploy cadence**: Dashboard UI can ship fixes without API restarts.
- **Separate scaling**: The API handles scrape workloads; the dashboard is a low-traffic read workload.
- **Simpler API server**: No Jinja2/SSR complexity in the FastAPI codebase.
- **CORS boundary**: Dashboard origin is explicitly allowed; no wildcard CORS.

## 4. Architecture Diagram

```text
┌──────────────┐     HTTPS      ┌──────────────┐     HTTPS      ┌──────────────────┐
│              │  Authorization │              │               │                  │
│   Dashboard  │ ──────────────>│   Nginx/CDN  │ ─────────────>│  ASB Cloud API   │
│   (React SPA)│   Bearer token │   (static)   │  /v1/* APIs   │  (FastAPI)       │
│              │ <──────────────│              │ <─────────────│                  │
└──────────────┘                └──────────────┘               └──────────────────┘
                                                                     │
                                        ┌────────────────────────────┼──────────┐
                                        │                            │          │
                                        v                            v          v
                                  ┌──────────┐              ┌──────────┐  ┌─────────┐
                                  │PostgreSQL│              │  Stripe  │  │  Redis  │
                                  │ (usage, │              │ (billing)│  │(post-v1)│
                                  │ sessions│              │          │  │         │
                                  │  keys)  │              └──────────┘  └─────────┘
                                  └──────────┘
```

## 5. Page Inventory

### Phase 1: Read-Only (MVP Dashboard)

| Page | Route | Purpose | API Endpoints Used |
|------|-------|---------|--------------------|
| Login | `/login` | Enter API key to authenticate | `POST /v1/dashboard/auth` (new) |
| Overview | `/` | Key tier, usage summary, recent activity | `GET /v1/usage`, `GET /v1/dashboard/stats` (new) |
| Usage | `/usage` | Usage charts (daily/weekly/monthly), top domains | `GET /v1/dashboard/usage/history` (new), `GET /v1/dashboard/usage/domains` (new) |
| Sessions | `/sessions` | Active sessions list, terminate sessions | `GET /v1/dashboard/sessions` (new), `DELETE /v1/sessions/{id}` (existing) |
| API Keys | `/keys` | View key details, copy key | `GET /v1/dashboard/keys/{key_id}` (new) |
| Billing | `/billing` | Current plan, invoices, billing portal link | `GET /v1/billing/invoices` (existing), `GET /v1/billing/portal` (existing) |
| Docs | `/docs` | Embedded API reference (Swagger UI iframe or custom) | Redirect to `/docs` (FastAPI built-in) |

### Phase 2: CRUD Operations

| Page | Route | Purpose | API Endpoints Used |
|------|-------|---------|--------------------|
| API Keys (full CRUD) | `/keys` | Create, rotate, revoke keys | `POST /v1/keys` (new), `DELETE /v1/keys/{key_id}` (new) |
| Plan / Upgrade | `/plan` | View tiers, upgrade/downgrade, view overage | `POST /v1/billing/checkout` (existing), `GET /v1/dashboard/overage` (new) |
| Settings | `/settings` | Notification preferences, webhook URLs | `PATCH /v1/dashboard/settings` (new) |
| Team (enterprise) | `/team` | Manage team members, sub-keys | `POST /v1/dashboard/team` (new, enterprise only) |

## 6. API Endpoints Needed by Dashboard

### 6.1 Existing Endpoints (no changes needed)

| Method | Path | Used By |
|--------|------|---------|
| `GET` | `/v1/usage` | Overview, Usage pages |
| `GET` | `/v1/sessions/{id}` | Session detail view |
| `DELETE` | `/v1/sessions/{id}` | Terminate session |
| `GET` | `/v1/billing/portal` | Billing page (Stripe portal link) |
| `GET` | `/v1/billing/invoices` | Billing page (invoice list) |
| `POST` | `/v1/billing/checkout` | Plan upgrade page |
| `GET` | `/v1/health` | Dashboard health indicator |

### 6.2 New Endpoints Required

#### Authentication

**`POST /v1/dashboard/auth`**

Validate an API key and return key profile. This is a dedicated auth endpoint that returns richer data than `get_api_key` (which only returns `key_id`). The dashboard uses this to get tier, email, and subscription status on login.

```http
POST /v1/dashboard/auth
Authorization: Bearer sk_live_<key>
```

Response (200):
```json
{
  "key_id": "key_abc123",
  "tier": "starter",
  "owner_email": "user@example.com",
  "created_at": "2026-01-15T00:00:00Z",
  "stripe_customer_id": "cus_xxx",
  "subscription_status": "active",
  "license_type": null,
  "has_active_license": false
}
```

Error (403):
```json
{
  "error_code": "INVALID_API_KEY",
  "message": "API key is invalid or revoked"
}
```

Implementation note: This endpoint reuses `get_api_key` to verify the key, then calls `key_store.get(key_id)` to return profile info. For PostgreSQL mode, it returns billing fields. For in-memory mode, billing fields are null.

---

#### Key Profile

**`GET /v1/dashboard/keys/{key_id}`**

Get full key profile including billing status. Requires authentication, and the authenticated key must match `key_id` (self-service only; admin panel would be separate).

Response (200):
```json
{
  "key_id": "key_abc123",
  "tier": "starter",
  "owner_email": "user@example.com",
  "created_at": "2026-01-15T00:00:00Z",
  "revoked": false,
  "stripe_customer_id": "cus_xxx",
  "stripe_subscription_id": "sub_xxx",
  "subscription_status": "active",
  "license_type": null,
  "license_key": null
}
```

---

#### Dashboard Statistics

**`GET /v1/dashboard/stats`**

Aggregate statistics for the current billing period.

Response (200):
```json
{
  "tier": "starter",
  "period": {
    "start": "2026-05-01T00:00:00Z",
    "end": "2026-05-31T23:59:59Z"
  },
  "requests": {
    "total": 15832,
    "limit": 25000,
    "percent_used": 63.3,
    "success_rate": 94.2,
    "avg_duration_ms": 2340
  },
  "sessions": {
    "active": 3,
    "max": 10
  },
  "blocks": {
    "total": 89,
    "rate": 0.6
  }
}
```

Implementation note: Queries `daily_usage` table for aggregated stats, `usage_records` for success rate, `sessions` for active count.

---

#### Usage History

**`GET /v1/dashboard/usage/history?days=30`**

Daily usage breakdown for chart rendering. Query parameter `days` defaults to 30 (max 90).

Response (200):
```json
{
  "key_id": "key_abc123",
  "tier": "starter",
  "days": [
    {
      "date": "2026-05-30",
      "requests": 482,
      "duration_ms": 1102340,
      "blocks": 3,
      "success_rate": 96.5
    },
    {
      "date": "2026-05-29",
      "requests": 391,
      "duration_ms": 890120,
      "blocks": 1,
      "success_rate": 97.2
    }
  ]
}
```

Implementation note: Queries `daily_usage` for aggregated data, falls back to `usage_records` if daily rollup hasn't run yet.

---

#### Top Domains

**`GET /v1/dashboard/usage/domains?days=30&limit=10`**

Most scraped domains in the given period.

Response (200):
```json
{
  "key_id": "key_abc123",
  "domains": [
    {"domain": "amazon.co.jp", "requests": 3200, "avg_duration_ms": 1800},
    {"domain": "rakuten.co.jp", "requests": 2100, "avg_duration_ms": 2400},
    {"domain": "yahoo.co.jp", "requests": 890, "avg_duration_ms": 3100}
  ]
}
```

Implementation note: Queries `usage_records` with `GROUP BY domain ORDER BY COUNT(*) DESC LIMIT $limit`.

---

#### Session List

**`GET /v1/dashboard/sessions`**

List all active (non-deleted, non-expired) sessions for the authenticated key.

Response (200):
```json
{
  "key_id": "key_abc123",
  "sessions": [
    {
      "session_id": "sess_abc123",
      "region": "jp",
      "fingerprint": "japan_ecommerce",
      "request_count": 47,
      "created_at": "2026-05-30T14:00:00Z",
      "last_used": "2026-05-30T14:05:30Z",
      "expires_at": "2026-05-30T14:10:00Z"
    }
  ]
}
```

Implementation note: Queries `sessions` table with `WHERE key_id = $1 AND deleted_at IS NULL AND expires_at > NOW() ORDER BY last_used DESC`.

---

#### Overage Status

**`GET /v1/dashboard/overage`**

Check current overage status for paid tiers.

Response (200):
```json
{
  "tier": "starter",
  "monthly_included": 25000,
  "used": 32100,
  "overage_requests": 7100,
  "overage_cost_usd": 14.20,
  "overage_rate": 0.002,
  "period_end": "2026-05-31T23:59:59Z"
}
```

Error (402, if overage blocking is active):
```json
{
  "error_code": "OVERAGE_LIMIT_EXCEEDED",
  "message": "Usage limit exceeded. Upgrade or pay overage.",
  "overage_cost_usd": 14.20
}
```

---

### 6.3 Endpoint Summary

| Endpoint | Method | Phase | New/Existing | Auth |
|----------|--------|-------|-------------|------|
| `/v1/dashboard/auth` | POST | 1 | **New** | Bearer |
| `/v1/dashboard/keys/{key_id}` | GET | 1 | **New** | Bearer |
| `/v1/dashboard/stats` | GET | 1 | **New** | Bearer |
| `/v1/dashboard/usage/history` | GET | 1 | **New** | Bearer |
| `/v1/dashboard/usage/domains` | GET | 1 | **New** | Bearer |
| `/v1/dashboard/sessions` | GET | 1 | **New** | Bearer |
| `/v1/dashboard/overage` | GET | 1 | **New** | Bearer |
| `/v1/usage` | GET | 1 | Existing | Bearer |
| `/v1/sessions/{id}` | GET | 1 | Existing | Bearer |
| `/v1/sessions/{id}` | DELETE | 1 | Existing | Bearer |
| `/v1/billing/portal` | GET | 1 | Existing | Bearer |
| `/v1/billing/invoices` | GET | 1 | Existing | Bearer |
| `/v1/billing/checkout` | POST | 2 | Existing | Bearer |
| `/v1/keys` | POST | 2 | **New** | Bearer |
| `/v1/keys/{key_id}` | DELETE | 2 | **New** | Bearer |
| `/v1/dashboard/settings` | PATCH | 2 | **New** | Bearer |
| `/v1/dashboard/team` | POST | 2 | **New** | Bearer (enterprise) |

## 7. CORS Configuration

The SPEC requires CORS for explicit dashboard origins only, not wildcard. Implementation:

```python
# In asb_api/__main__.py, after creating the FastAPI app:
from fastapi.middleware.cors import CORSMiddleware

DASHBOARD_ORIGINS = os.environ.get("DASHBOARD_ORIGINS", "").split(",")
if DASHBOARD_ORIGINS and DASHBOARD_ORIGINS[0]:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=DASHBOARD_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
```

Environment configuration:
```bash
# Single origin (local dev):
DASHBOARD_ORIGINS=http://localhost:5173

# Multiple origins (production):
DASHBOARD_ORIGINS=https://dashboard.asbcloud.io,https://app.asbcloud.io

# Empty / not set = no CORS middleware (current behavior, secure by default).
```

Config file addition (`config.yaml`):
```yaml
dashboard:
  enabled: false
  origins: "${DASHBOARD_ORIGINS}"
```

## 8. Auth Flow

### 8.1 Login

```text
User opens dashboard -> Login page
  |
  v
User enters API key (sk_live_* or sk_test_*)
  |
  v
Dashboard calls POST /v1/dashboard/auth with Bearer token
  |
  ├─ 200: Store key_id + tier + email in sessionStorage
  │       Redirect to Overview
  │
  └─ 403: Show "Invalid API key" error
```

### 8.2 Session Persistence

- The raw API key is stored in `sessionStorage` (cleared on tab close).
- On page refresh, the dashboard reads the key from `sessionStorage` and calls `POST /v1/dashboard/auth` to re-validate.
- If validation fails (key revoked), redirect to login.
- No refresh tokens or OAuth flow in Phase 1 — keep it simple with bearer tokens.

### 8.3 Magic-Link / OAuth (Phase 3, optional)

For Phase 3, if a non-API-key login is desired:

- Magic link: User enters email, API sends a signed link to their email. Clicking the link returns a short-lived JWT. JWT used as bearer token to call API.
- OAuth (Google/GitHub): After OAuth callback, API creates an API key for the user and returns it. User is logged in as that key.
- **Decision**: Deferred. Phase 1-2 use API key login exclusively. Evaluate magic-link demand after dashboard has active users.

## 9. Dashboard Self-Hosted Deployment

Self-hosted operators can deploy the dashboard as a Docker container alongside the API:

```yaml
# docker-compose.yml (example)
services:
  api:
    image: asb-cloud-api:latest
    environment:
      DATABASE_URL: postgres://...
      DASHBOARD_ORIGINS: https://dashboard.mydomain.com
    ports:
      - "8000:8000"

  dashboard:
    image: asb-cloud-dashboard:latest
    environment:
      VITE_API_URL: https://api.mydomain.com
    ports:
      - "3000:80"
```

The dashboard Docker image is a simple Nginx + SPA static build:

```dockerfile
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

## 10. Implementation Phases

### Phase 1: Read-Only MVP (first release after v1 API stability)

**API work:**
- [ ] Add `POST /v1/dashboard/auth` endpoint
- [ ] Add `GET /v1/dashboard/keys/{key_id}` endpoint
- [ ] Add `GET /v1/dashboard/stats` endpoint
- [ ] Add `GET /v1/dashboard/usage/history` endpoint
- [ ] Add `GET /v1/dashboard/usage/domains` endpoint
- [ ] Add `GET /v1/dashboard/sessions` endpoint
- [ ] Add `GET /v1/dashboard/overage` endpoint
- [x] Add CORS middleware gated by `DASHBOARD_ORIGINS` / `dashboard.origins` (explicit list, no wildcard; secure default when unset)
- [x] Add `dashboard` config block to `config.yaml` + env support + regression test

**Frontend work:**
- [ ] Project scaffold (React + TypeScript + Vite + Tailwind)
- [ ] Login page
- [ ] Overview page with tier badge + summary stats
- [ ] Usage page with daily bar chart + top domains table
- [ ] Sessions page with active session list + terminate action
- [ ] API Keys page with key details view
- [ ] Billing page with current plan + invoice list + Stripe portal link
- [ ] Navigation shell (sidebar + header)

**Tests:**
- [ ] Dashboard auth endpoint returns 200 for valid key, 403 for invalid
- [ ] Stats/history/domains/sessions endpoints return correct shapes
- [ ] CORS headers present when DASHBOARD_ORIGINS is set
- [ ] CORS headers absent when DASHBOARD_ORIGINS is empty (secure by default)

### Phase 2: CRUD + Plan Management

- [ ] Key creation (POST /v1/keys) with tier selection
- [ ] Key rotation and revocation
- [ ] Upgrade/downgrade flow with Stripe checkout
- [ ] Settings page (webhook URLs, notification prefs)
- [ ] Enterprise team management

### Phase 3: Advanced (optional)

- [ ] Magic-link or OAuth login
- [ ] Team sub-keys with usage quotas
- [ ] Scrape playground (interactive API console)
- [ ] Alert config (email on high block rate, overage warnings)
- [ ] Public status page for API health

## 11. Dashboard Routes File Structure

The new dashboard API endpoints should be added as a dedicated routes file:

```
asb_api/api/routes/dashboard.py    # All /v1/dashboard/* endpoints
```

This keeps dashboard-specific logic separate from the existing route files and avoids polluting `scrape.py`, `sessions.py`, or `usage.py` with dashboard-only queries.

### Backend Implementation Notes

The dashboard routes file imports existing stores:

```python
# asb_api/api/routes/dashboard.py
from fastapi import APIRouter, Depends
from asb_api.api.auth import get_api_key, get_key_store
from asb_api.api.routes.usage import get_usage_context
from asb_api.api.routes.sessions import get_session_store

router = APIRouter()

@router.post("/v1/dashboard/auth")
async def dashboard_auth(key_id: str = Depends(get_api_key)):
    store = get_key_store()
    ...
```

Store accessors follow the same pattern as existing routes (global module variables set at startup).

## 12. Decision Log

| Date | Decision | Owner |
|------|----------|-------|
| 2026-05-30 | Dashboard is a separate React SPA deployable, not embedded in the FastAPI server. | Maintainer |
| 2026-05-30 | Dashboard auth uses existing API key bearer tokens (no separate auth system). | Maintainer |
| 2026-05-30 | Phase 1 focuses on read-only views; key CRUD and plan changes are Phase 2. | Maintainer |
| 2026-05-30 | Magic-link and OAuth login are deferred to Phase 3. | Maintainer |
| 2026-05-30 | CORS is configured for explicit origins only, not wildcard. Gated by `DASHBOARD_ORIGINS` env var. | Maintainer |
| 2026-05-30 | Dashboard API endpoints are in `asb_api/api/routes/dashboard.py`, separate from existing route files. | Maintainer |

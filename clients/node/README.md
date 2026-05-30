# asb-cloud-client (Node.js)

Thin Node.js / TypeScript client for the [ASB Cloud API](https://github.com/shanewas/asb-cloud-api).

## Installation

```bash
npm install asb-cloud-client
# or
yarn add asb-cloud-client
```

From monorepo source (for development):

```bash
cd clients/node
npm link
```

## Quick Start

```js
const { AsbClient, AsbError } = require('asb-cloud-client');

const client = new AsbClient({
  baseUrl: 'http://localhost:8000',
  apiKey: process.env.ASB_API_KEY || 'sk_live_...'
});

async function main() {
  // Health (no auth)
  const h = await client.health();
  console.log('Health:', h.status);

  // Scrape
  const result = await client.scrape({
    url: 'https://example.com',
    method: 'GET',
    region: 'jp',
    timeout: 30
  });
  console.log('HTML length:', result.html?.length);

  // Session
  const sess = await client.createSession({ region: 'jp' });
  console.log('Session:', sess.session_id);

  // Usage
  const usage = await client.getUsage();
  console.log('Usage:', usage.requests_used, '/', usage.requests_limit);

  // Billing portal
  try {
    const portal = await client.getBillingPortal();
    console.log('Portal:', portal.portal_url);
  } catch (e) {
    if (e instanceof AsbError) console.log('Billing unavailable:', e.message);
  }
}

main().catch(console.error);
```

## API

- `new AsbClient({ baseUrl?, apiKey?, timeout? })`
- `client.health()`
- `client.scrape({ url, method?, ... })` — full scrape options
- `client.createSession({ region?, fingerprint? })`
- `client.getSession(sessionId)`
- `client.deleteSession(sessionId)`
- `client.getUsage()`
- `client.getBillingPortal()`

All methods are async and throw `AsbError` (with `.statusCode`, `.errorCode`, `.message`).

See main [clients/README.md](../README.md) and [SPEC.md](../../../SPEC.md) for shapes and error codes.

## Examples

See `../../examples/node/` for runnable smoke tests against local self-hosted API.

#!/usr/bin/env node
/**
 * Basic smoke test for Node client against local self-hosted ASB API.
 */
const { AsbClient, AsbError } = require('../../clients/node');

async function main() {
  const base = process.env.ASB_BASE_URL || 'http://localhost:8000';
  const key = process.env.ASB_API_KEY;
  if (!key) {
    console.error('ERROR: Set ASB_API_KEY (from API startup logs)');
    process.exit(1);
  }

  const client = new AsbClient({ baseUrl: base, apiKey: key });

  console.log('== Health ==');
  console.log(await client.health());

  console.log('\n== Scrape ==');
  try {
    const r = await client.scrape({ url: 'https://example.com', method: 'GET', region: 'jp', timeout: 20 });
    console.log('status:', r.status);
    console.log('html_len:', (r.html || '').length);
    console.log('request_id:', r.metadata && r.metadata.request_id);
  } catch (e) {
    if (e instanceof AsbError) {
      console.error('Scrape error:', e.message, e.statusCode);
      process.exit(2);
    }
    throw e;
  }

  console.log('\n== Session create/get/delete ==');
  try {
    const s = await client.createSession({ region: 'jp' });
    console.log('created:', s.session_id);
    console.log(await client.getSession(s.session_id));
    await client.deleteSession(s.session_id);
    console.log('deleted');
  } catch (e) {
    if (e instanceof AsbError) console.log('Session flow (may fail without persistence):', e.message);
  }

  console.log('\n== Usage ==');
  try {
    console.log(await client.getUsage());
  } catch (e) {
    if (e instanceof AsbError) console.log('Usage not available:', e.message);
  }

  console.log('\n== Billing portal ==');
  try {
    console.log(await client.getBillingPortal());
  } catch (e) {
    if (e instanceof AsbError) console.log('Billing portal not available in this mode:', e.message);
  }

  console.log('\nNode examples completed.');
}

main().catch(err => { console.error(err); process.exit(1); });

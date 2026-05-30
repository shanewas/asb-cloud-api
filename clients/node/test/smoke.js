/**
 * Tiny smoke test for the Node client (CJS).
 * Run with: npm test (from clients/node)
 */
const assert = require('node:assert');
const { AsbClient, AsbError, AsbAuthError } = require('../index.js');

assert.strictEqual(typeof AsbClient, 'function', 'AsbClient should be exported');
assert.strictEqual(typeof AsbError, 'function', 'AsbError should be exported');
assert.strictEqual(typeof AsbAuthError, 'function', 'AsbAuthError should be exported');

const client = new AsbClient({ baseUrl: 'http://localhost:1234', apiKey: 'sk_test_dummy' });
assert.strictEqual(typeof client.health, 'function', 'health method exists');
assert.strictEqual(typeof client.scrape, 'function', 'scrape method exists');

console.log('Node client CJS smoke test passed (imports + instantiate OK).');

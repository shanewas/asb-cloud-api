/**
 * Thin Node.js client for ASB Cloud API v1.
 * Requires Node >= 18 (global fetch).
 */

export class AsbError extends Error {
  constructor(message, statusCode = null, errorCode = null, response = null) {
    super(message);
    this.name = 'AsbError';
    this.statusCode = statusCode;
    this.errorCode = errorCode;
    this.response = response || {};
  }
}

export class AsbAuthError extends AsbError {
  constructor(message, statusCode = 403, opts = {}) {
    super(message, statusCode, opts.errorCode || 'INVALID_API_KEY', opts.response);
    this.name = 'AsbAuthError';
  }
}

export class AsbRateLimitError extends AsbError {
  constructor(message, statusCode = 429, opts = {}) {
    super(message, statusCode, 'RATE_LIMIT_EXCEEDED', opts.response);
    this.name = 'AsbRateLimitError';
    this.limit = opts.limit;
    this.remaining = opts.remaining;
    this.resetAt = opts.resetAt;
  }
}

export class AsbOverageError extends AsbError {
  constructor(message, statusCode = 402, opts = {}) {
    super(message, statusCode, 'OVERAGE_LIMIT_EXCEEDED', opts.response);
    this.name = 'AsbOverageError';
    this.overageCostUsd = opts.overageCostUsd;
  }
}

export class AsbNotFoundError extends AsbError {
  constructor(message, statusCode = 404, opts = {}) {
    super(message, statusCode, opts.errorCode, opts.response);
    this.name = 'AsbNotFoundError';
  }
}

export class AsbClient {
  /**
   * @param {Object} opts
   * @param {string} [opts.baseUrl='http://localhost:8000']
   * @param {string} [opts.apiKey] - or set ASB_API_KEY env
   * @param {number} [opts.timeout=60000]
   * @param {Object} [opts.headers]
   */
  constructor(opts = {}) {
    this.baseUrl = (opts.baseUrl || 'http://localhost:8000').replace(/\/$/, '') + '/';
    this.apiKey = opts.apiKey || process.env.ASB_API_KEY || null;
    this.timeout = opts.timeout || 60000;
    this.headers = {
      'User-Agent': 'asb-cloud-client/0.1.0 (node)',
      'Content-Type': 'application/json',
      ...(opts.headers || {}),
    };
    if (this.apiKey) {
      this.headers['Authorization'] = `Bearer ${this.apiKey}`;
    }
  }

  _url(path) {
    return new URL(path.replace(/^\//, ''), this.baseUrl).toString();
  }

  async _request(method, path, body = null) {
    const url = this._url(path);
    const init = {
      method,
      headers: { ...this.headers },
      signal: AbortSignal.timeout ? AbortSignal.timeout(this.timeout) : undefined,
    };
    if (body && (method === 'POST' || method === 'PUT' || method === 'PATCH')) {
      init.body = JSON.stringify(body);
    }

    let resp;
    try {
      resp = await fetch(url, init);
    } catch (e) {
      throw new AsbError(`Network error: ${e.message}`);
    }

    let data = null;
    const text = await resp.text();
    try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }

    if (resp.ok) {
      return data;
    }

    const status = resp.status;
    let message = (data && (data.message || (data.detail && data.detail.message))) || text || `HTTP ${status}`;
    let errorCode = (data && data.error_code) || (data && data.detail && data.detail.error_code);

    if (status === 403) {
      throw new AsbAuthError(message, status, { errorCode, response: data });
    }
    if (status === 404) {
      throw new AsbNotFoundError(message, status, { errorCode, response: data });
    }
    if (status === 429) {
      const d = data.detail || data;
      throw new AsbRateLimitError(message, status, {
        limit: d.limit, remaining: d.remaining, resetAt: d.reset_at, response: data
      });
    }
    if (status === 402) {
      const d = data.detail || data;
      throw new AsbOverageError(message, status, { overageCostUsd: d.overage_cost_usd, response: data });
    }

    throw new AsbError(message, status, errorCode, data);
  }

  // --- Public methods ---

  async health() {
    return this._request('GET', '/v1/health');
  }

  async scrape(opts) {
    if (!opts || !opts.url) throw new Error('scrape requires { url }');
    const payload = {
      url: opts.url,
      method: opts.method || 'GET',
      headers: opts.headers,
      data: opts.data,
      proxy_provider: opts.proxyProvider,
      region: opts.region,
      fingerprint: opts.fingerprint,
      timeout: opts.timeout ?? 30,
      screenshot: !!opts.screenshot,
      session_id: opts.sessionId,
      session_type: opts.sessionType || 'stateless',
    };
    // remove undefined
    Object.keys(payload).forEach(k => payload[k] === undefined && delete payload[k]);
    return this._request('POST', '/v1/scrape', payload);
  }

  async createSession(opts = {}) {
    return this._request('POST', '/v1/sessions', {
      region: opts.region || 'jp',
      fingerprint: opts.fingerprint,
    });
  }

  async getSession(sessionId) {
    if (!sessionId) throw new Error('getSession requires sessionId');
    return this._request('GET', `/v1/sessions/${sessionId}`);
  }

  async deleteSession(sessionId) {
    if (!sessionId) throw new Error('deleteSession requires sessionId');
    await this._request('DELETE', `/v1/sessions/${sessionId}`);
  }

  async getUsage() {
    return this._request('GET', '/v1/usage');
  }

  async getBillingPortal() {
    return this._request('GET', '/v1/billing/portal');
  }
}

// CommonJS friendly default export
module.exports = {
  AsbClient: AsbClient,
  AsbError,
  AsbAuthError,
  AsbRateLimitError,
  AsbOverageError,
  AsbNotFoundError,
};
module.exports.default = AsbClient;

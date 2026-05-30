export interface AsbClientOptions {
  baseUrl?: string;
  apiKey?: string;
  timeout?: number;
  headers?: Record<string, string>;
}

export interface ScrapeOptions {
  url: string;
  method?: 'GET' | 'POST';
  headers?: Record<string, string>;
  data?: any;
  proxyProvider?: string | null;
  region?: string | null;
  fingerprint?: string | null;
  timeout?: number;
  screenshot?: boolean;
  sessionId?: string | null;
  sessionType?: 'stateless' | 'stateful' | 'stateful_reset';
}

export interface SessionCreateOptions {
  region?: string;
  fingerprint?: string | null;
}

export class AsbError extends Error {
  statusCode: number | null;
  errorCode: string | null;
  response: any;
}

export class AsbAuthError extends AsbError {}
export class AsbRateLimitError extends AsbError {
  limit?: number;
  remaining?: number;
  resetAt?: number;
}
export class AsbOverageError extends AsbError {
  overageCostUsd?: number;
}
export class AsbNotFoundError extends AsbError {}

export class AsbClient {
  constructor(opts?: AsbClientOptions);
  health(): Promise<any>;
  scrape(opts: ScrapeOptions): Promise<any>;
  createSession(opts?: SessionCreateOptions): Promise<any>;
  getSession(sessionId: string): Promise<any>;
  deleteSession(sessionId: string): Promise<void>;
  getUsage(): Promise<any>;
  getBillingPortal(): Promise<any>;
}

import type { Page } from "playwright";

export type JobStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "skipped";

export interface OwnedIdentity {
  id: string;
  email: string;
  password: string;
  fullName: string;
  phoneE164?: string;
  postalCode?: string;
  prefecture?: string;
  city?: string;
  addressLine1?: string;
  notes?: string;
}

export interface ProxyEndpoint {
  raw: string;
  server: string;
  username?: string;
  password?: string;
}

export interface BrowserSession {
  profileId: string;
  wsEndpoint?: string;
  /** Present when using the local Playwright browser provider. */
  page?: Page;
  close: () => Promise<void>;
}

export interface RegisterResult {
  marketplace: string;
  email: string;
  identityId: string;
  profileId: string;
  proxy?: string;
  status: "created" | "already_exists" | "dry_run";
  externalAccountId?: string;
  metadata?: Record<string, unknown>;
}

export interface AccountRecord extends RegisterResult {
  createdAt: string;
  updatedAt: string;
  lastError?: string;
}

export interface Job {
  id: string;
  identity: OwnedIdentity;
  proxy?: ProxyEndpoint;
  attempt: number;
  status: JobStatus;
  error?: string;
  result?: RegisterResult;
}

export interface ImapSettings {
  host: string;
  port: number;
  secure: boolean;
  user: string;
  pass: string;
  mailbox: string;
  /** Optional From: filter substrings (case-insensitive). */
  fromIncludes: string[];
  /** Optional Subject: filter substrings (case-insensitive). */
  subjectIncludes: string[];
  /** How far back to search on each poll (ms). */
  lookbackMs: number;
  tlsRejectUnauthorized: boolean;
}

export interface SystemConfig {
  marketplace: string;
  locale: string;
  timezone: string;
  concurrency: number;
  maxRetries: number;
  delayBetweenJobsMs: { min: number; max: number };
  browser: {
    provider: "dry-run" | "cdp" | "playwright";
    headless: boolean;
    slowMoMs?: number;
  };
  mailbox: {
    provider: "dry-run" | "imap" | "prompt";
    otpTimeoutMs: number;
    pollIntervalMs: number;
    imap?: ImapSettings;
  };
  proxy: { required: boolean; sticky: boolean };
  store: { path: string };
  amazon?: {
    registerUrl?: string;
  };
}

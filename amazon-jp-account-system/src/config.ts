import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { z } from "zod";
import type { ImapSettings, SystemConfig } from "./types.js";
import { loadDotEnv } from "./utils/dotenv.js";

const ImapSchema = z.object({
  host: z.string().min(1),
  port: z.number().int().positive().default(993),
  secure: z.boolean().default(true),
  user: z.string().min(1),
  pass: z.string().min(1),
  mailbox: z.string().default("INBOX"),
  fromIncludes: z.array(z.string()).default(["amazon"]),
  subjectIncludes: z.array(z.string()).default([]),
  lookbackMs: z.number().int().positive().default(15 * 60_000),
  tlsRejectUnauthorized: z.boolean().default(true),
});

const ConfigSchema = z.object({
  marketplace: z.string().default("amazon.co.jp"),
  locale: z.string().default("ja-JP"),
  timezone: z.string().default("Asia/Tokyo"),
  concurrency: z.number().int().min(1).max(20).default(2),
  maxRetries: z.number().int().min(0).max(10).default(2),
  delayBetweenJobsMs: z
    .object({
      min: z.number().int().min(0),
      max: z.number().int().min(0),
    })
    .refine((v) => v.max >= v.min, {
      message: "delayBetweenJobsMs.max must be >= min",
    }),
  browser: z.object({
    provider: z.enum(["dry-run", "cdp", "playwright"]).default("dry-run"),
    headless: z.boolean().default(true),
    slowMoMs: z.number().int().min(0).optional(),
  }),
  mailbox: z.object({
    provider: z.enum(["dry-run", "imap", "prompt"]).default("dry-run"),
    otpTimeoutMs: z.number().int().positive().default(120_000),
    pollIntervalMs: z.number().int().positive().default(3_000),
    imap: ImapSchema.partial().optional(),
  }),
  proxy: z.object({
    required: z.boolean().default(false),
    sticky: z.boolean().default(true),
  }),
  store: z.object({
    path: z.string().default("data/accounts.json"),
  }),
  amazon: z
    .object({
      registerUrl: z.string().url().optional(),
    })
    .optional(),
});

function envFlag(name: string, fallback: boolean): boolean {
  const v = process.env[name];
  if (v === undefined || v === "") return fallback;
  return !["0", "false", "no", "off"].includes(v.toLowerCase());
}

function envCsv(name: string): string[] | undefined {
  const v = process.env[name];
  if (!v?.trim()) return undefined;
  return v
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

/** Resolve IMAP settings from config + environment variables. */
export function resolveImapSettings(
  partial?: Partial<ImapSettings>,
): ImapSettings {
  const host = process.env.IMAP_HOST ?? partial?.host;
  const user = process.env.IMAP_USER ?? partial?.user;
  const pass = process.env.IMAP_PASS ?? partial?.pass;
  if (!host || !user || !pass) {
    throw new Error(
      "IMAP requires host/user/pass. Set IMAP_HOST, IMAP_USER, IMAP_PASS " +
        "(or mailbox.imap in config).",
    );
  }

  const port = Number(process.env.IMAP_PORT ?? partial?.port ?? 993);
  const secure = envFlag("IMAP_SECURE", partial?.secure ?? port === 993);
  const mailbox = process.env.IMAP_MAILBOX ?? partial?.mailbox ?? "INBOX";
  const fromIncludes =
    envCsv("IMAP_FROM_INCLUDES") ?? partial?.fromIncludes ?? ["amazon"];
  const subjectIncludes =
    envCsv("IMAP_SUBJECT_INCLUDES") ?? partial?.subjectIncludes ?? [];
  const lookbackMs = Number(
    process.env.IMAP_LOOKBACK_MS ?? partial?.lookbackMs ?? 15 * 60_000,
  );
  const tlsRejectUnauthorized = envFlag(
    "IMAP_TLS_REJECT_UNAUTHORIZED",
    partial?.tlsRejectUnauthorized ?? true,
  );

  return ImapSchema.parse({
    host,
    port,
    secure,
    user,
    pass,
    mailbox,
    fromIncludes,
    subjectIncludes,
    lookbackMs,
    tlsRejectUnauthorized,
  });
}

export function loadConfig(configPath?: string): SystemConfig {
  loadDotEnv();
  const path = resolve(
    process.cwd(),
    configPath ?? process.env.CONFIG_PATH ?? "config/default.json",
  );
  const raw = JSON.parse(readFileSync(path, "utf8")) as unknown;
  const parsed = ConfigSchema.parse(raw);

  let imap: ImapSettings | undefined;
  try {
    imap = resolveImapSettings(parsed.mailbox.imap);
  } catch {
    imap = undefined;
  }

  return {
    ...parsed,
    mailbox: {
      ...parsed.mailbox,
      imap,
    },
  };
}

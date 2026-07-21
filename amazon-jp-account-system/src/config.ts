import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { z } from "zod";
import type { SystemConfig } from "./types.js";

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

export function loadConfig(configPath?: string): SystemConfig {
  const path = resolve(
    process.cwd(),
    configPath ?? process.env.CONFIG_PATH ?? "config/default.json",
  );
  const raw = JSON.parse(readFileSync(path, "utf8")) as unknown;
  return ConfigSchema.parse(raw);
}

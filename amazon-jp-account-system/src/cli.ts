#!/usr/bin/env node
import { Command } from "commander";
import { createAmazonAdapter } from "./adapters/amazon.js";
import { createBrowser } from "./adapters/browser.js";
import { loadOwnedIdentities } from "./adapters/identity.js";
import { createMailbox, ImapMailbox } from "./adapters/mailbox.js";
import { loadProxies } from "./adapters/proxy.js";
import { loadConfig } from "./config.js";
import { Orchestrator } from "./core/orchestrator.js";
import { AccountStore } from "./store/account-store.js";
import type { OwnedIdentity, SystemConfig } from "./types.js";
import { log } from "./utils/logger.js";

const program = new Command();

program
  .name("amz-jp")
  .description(
    "Account creation tool for amazon.co.jp using identities you control",
  )
  .version("0.1.0");

program
  .command("create-one")
  .description("Create a single amazon.co.jp account with Playwright")
  .option("--identity-id <id>", "identity id from identities JSON")
  .option("--email <email>", "email you own (inline identity)")
  .option("--password <password>", "account password")
  .option("--name <fullName>", "full name for the Amazon account")
  .option("--identities <path>", "path to owned identities JSON")
  .option("--proxies <path>", "path to proxy list")
  .option("--config <path>", "path to config JSON")
  .option("--headed", "show the browser window (recommended)", false)
  .option("--headless", "run browser headless", false)
  .option("--imap", "fetch OTP automatically via IMAP", false)
  .option("--otp-prompt", "type OTP in the terminal instead of IMAP", false)
  .option("--dry-run", "simulate without contacting Amazon", false)
  .option("--force", "create even if identity already exists in store", false)
  .action(async (opts: {
    identityId?: string;
    email?: string;
    password?: string;
    name?: string;
    identities?: string;
    proxies?: string;
    config?: string;
    headed: boolean;
    headless: boolean;
    imap: boolean;
    otpPrompt: boolean;
    dryRun: boolean;
    force: boolean;
  }) => {
    if (opts.imap && opts.otpPrompt) {
      throw new Error("Use only one of --imap or --otp-prompt");
    }

    const base = loadConfig(opts.config);
    const identity = resolveOneIdentity(opts);
    const proxies = loadProxies(opts.proxies);
    const mailboxProvider = resolveMailboxProvider(opts, base);

    const config: SystemConfig = {
      ...base,
      concurrency: 1,
      maxRetries: opts.dryRun ? base.maxRetries : Math.min(base.maxRetries, 1),
      browser: {
        ...base.browser,
        provider: opts.dryRun ? "dry-run" : "playwright",
        headless: opts.headless ? true : opts.headed ? false : base.browser.headless,
        slowMoMs: base.browser.slowMoMs ?? 40,
      },
      mailbox: {
        ...base.mailbox,
        provider: mailboxProvider,
      },
    };

    // Default create-one to headed so captcha/OTP UX is workable.
    if (!opts.dryRun && !opts.headless && !opts.headed) {
      config.browser.headless = false;
    }

    const storePath = process.env.ACCOUNTS_DB_PATH ?? config.store.path;
    const store = new AccountStore(storePath);

    if (!opts.force) {
      const existing =
        store.findByIdentityId(identity.id) ?? store.findByEmail(identity.email);
      if (existing && existing.status === "created") {
        log("info", "account already stored as created; use --force to retry", {
          identityId: identity.id,
          email: identity.email,
        });
        console.log(JSON.stringify(existing, null, 2));
        return;
      }
    }

    log("info", "creating one amazon.co.jp account", {
      identityId: identity.id,
      email: identity.email,
      dryRun: opts.dryRun,
      headless: config.browser.headless,
      mailbox: config.mailbox.provider,
    });

    const orchestrator = new Orchestrator(
      config,
      createBrowser(config.browser),
      createMailbox(config.mailbox),
      createAmazonAdapter(opts.dryRun),
      store,
    );

    const jobs = await orchestrator.run({
      identities: [identity],
      proxies,
      concurrency: 1,
      count: 1,
      dryRun: opts.dryRun,
      skipExisting: !opts.force,
    });

    const job = jobs[0];
    if (!job || job.status === "failed") {
      log("error", "create-one failed", {
        error: job?.error ?? "unknown error",
        email: identity.email,
      });
      process.exitCode = 1;
      return;
    }

    console.log(JSON.stringify(job.result ?? store.findByIdentityId(identity.id), null, 2));
  });

program
  .command("test-imap")
  .description("Verify IMAP login and mailbox access")
  .option("--config <path>", "path to config JSON")
  .action(async (opts: { config?: string }) => {
    const config = loadConfig(opts.config);
    const mailbox = new ImapMailbox({
      ...config.mailbox,
      provider: "imap",
    });
    const result = await mailbox.ping();
    log("info", "IMAP connection ok", result);
    console.log(JSON.stringify({ ok: true, ...result }, null, 2));
  });

program
  .command("run")
  .description("Run parallel account jobs for owned identities")
  .option("-c, --concurrency <n>", "max parallel workers", (v) => Number(v))
  .option("-n, --count <n>", "max identities to process", (v) => Number(v))
  .option("--dry-run", "simulate flows without calling Amazon", false)
  .option("--config <path>", "path to config JSON")
  .option("--identities <path>", "path to owned identities JSON")
  .option("--proxies <path>", "path to proxy list")
  .option("--force", "do not skip identities already in the store", false)
  .action(async (opts: {
    concurrency?: number;
    count?: number;
    dryRun: boolean;
    config?: string;
    identities?: string;
    proxies?: string;
    force: boolean;
  }) => {
    const config = loadConfig(opts.config);
    const identities = loadOwnedIdentities(opts.identities);
    const proxies = loadProxies(opts.proxies);
    const dryRun = opts.dryRun || config.browser.provider === "dry-run";
    const effectiveConfig: SystemConfig = dryRun
      ? {
          ...config,
          browser: { ...config.browser, provider: "dry-run" },
          mailbox: { ...config.mailbox, provider: "dry-run" },
        }
      : config;

    const storePath = process.env.ACCOUNTS_DB_PATH ?? effectiveConfig.store.path;
    const store = new AccountStore(storePath);
    const orchestrator = new Orchestrator(
      effectiveConfig,
      createBrowser(effectiveConfig.browser),
      createMailbox(effectiveConfig.mailbox),
      createAmazonAdapter(dryRun),
      store,
    );

    const jobs = await orchestrator.run({
      identities,
      proxies,
      concurrency: opts.concurrency,
      count: opts.count,
      dryRun,
      skipExisting: !opts.force,
    });

    const failed = jobs.filter((j) => j.status === "failed");
    if (failed.length > 0) {
      log("error", "one or more jobs failed", {
        failed: failed.map((j) => ({
          id: j.id,
          email: j.identity.email,
          error: j.error,
        })),
      });
      process.exitCode = 1;
    }
  });

program
  .command("list")
  .description("List accounts saved in the local store")
  .option("--config <path>", "path to config JSON")
  .action((opts: { config?: string }) => {
    const config = loadConfig(opts.config);
    const storePath = process.env.ACCOUNTS_DB_PATH ?? config.store.path;
    const store = new AccountStore(storePath);
    const rows = store.list();
    console.log(JSON.stringify(rows, null, 2));
  });

function resolveMailboxProvider(
  opts: { dryRun: boolean; imap: boolean; otpPrompt: boolean },
  base: SystemConfig,
): SystemConfig["mailbox"]["provider"] {
  if (opts.dryRun) return "dry-run";
  if (opts.imap) return "imap";
  if (opts.otpPrompt) return "prompt";
  if (base.mailbox.provider === "imap" || base.mailbox.imap) return "imap";
  if (base.mailbox.provider === "prompt") return "prompt";
  // Prefer IMAP when env credentials exist; otherwise prompt.
  if (process.env.IMAP_HOST && process.env.IMAP_USER && process.env.IMAP_PASS) {
    return "imap";
  }
  return "prompt";
}

function resolveOneIdentity(opts: {
  identityId?: string;
  email?: string;
  password?: string;
  name?: string;
  identities?: string;
}): OwnedIdentity {
  if (opts.email || opts.password || opts.name) {
    if (!opts.email || !opts.password || !opts.name) {
      throw new Error(
        "Inline identity requires --email, --password, and --name together",
      );
    }
    return {
      id: opts.identityId ?? `inline-${opts.email}`,
      email: opts.email,
      password: opts.password,
      fullName: opts.name,
    };
  }

  const identities = loadOwnedIdentities(opts.identities);
  if (opts.identityId) {
    const found = identities.find((i) => i.id === opts.identityId);
    if (!found) {
      throw new Error(`Identity not found: ${opts.identityId}`);
    }
    return found;
  }
  return identities[0];
}

program.parseAsync(process.argv).catch((err: unknown) => {
  log("error", "fatal", {
    error: err instanceof Error ? err.message : String(err),
  });
  process.exit(1);
});

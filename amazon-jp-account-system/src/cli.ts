#!/usr/bin/env node
import { Command } from "commander";
import { createAmazonAdapter } from "./adapters/amazon.js";
import { createBrowser } from "./adapters/browser.js";
import { loadOwnedIdentities } from "./adapters/identity.js";
import { createMailbox } from "./adapters/mailbox.js";
import { loadProxies } from "./adapters/proxy.js";
import { loadConfig } from "./config.js";
import { Orchestrator } from "./core/orchestrator.js";
import { AccountStore } from "./store/account-store.js";
import { log } from "./utils/logger.js";

const program = new Command();

program
  .name("amz-jp")
  .description(
    "Own-system orchestrator for parallel amazon.co.jp account workflows using identities you control",
  )
  .version("0.1.0");

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

    const storePath =
      process.env.ACCOUNTS_DB_PATH ?? config.store.path;
    const store = new AccountStore(storePath);
    const orchestrator = new Orchestrator(
      config,
      createBrowser(config.browser),
      createMailbox(config.mailbox),
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

program.parseAsync(process.argv).catch((err: unknown) => {
  log("error", "fatal", {
    error: err instanceof Error ? err.message : String(err),
  });
  process.exit(1);
});

import pLimit from "p-limit";
import type { AmazonAdapter } from "../adapters/amazon.js";
import type { BrowserAdapter } from "../adapters/browser.js";
import type { MailboxAdapter } from "../adapters/mailbox.js";
import { assignStickyProxy } from "../adapters/proxy.js";
import type { AccountStore } from "../store/account-store.js";
import type {
  Job,
  OwnedIdentity,
  ProxyEndpoint,
  SystemConfig,
} from "../types.js";
import { log, randomBetween, sleep } from "../utils/logger.js";

export interface RunOptions {
  identities: OwnedIdentity[];
  proxies: ProxyEndpoint[];
  concurrency?: number;
  count?: number;
  dryRun: boolean;
  skipExisting?: boolean;
}

export class Orchestrator {
  constructor(
    private readonly config: SystemConfig,
    private readonly browser: BrowserAdapter,
    private readonly mailbox: MailboxAdapter,
    private readonly amazon: AmazonAdapter,
    private readonly store: AccountStore,
  ) {}

  async run(options: RunOptions): Promise<Job[]> {
    const selected = options.identities.slice(
      0,
      options.count ?? options.identities.length,
    );
    const concurrency = options.concurrency ?? this.config.concurrency;
    const limit = pLimit(concurrency);
    const jobs: Job[] = selected.map((identity, index) => ({
      id: `job-${identity.id}`,
      identity,
      proxy: this.config.proxy.sticky
        ? assignStickyProxy(options.proxies, index)
        : options.proxies[index],
      attempt: 0,
      status: "pending",
    }));

    if (this.config.proxy.required) {
      for (const job of jobs) {
        if (!job.proxy) {
          job.status = "failed";
          job.error = "proxy.required is true but no proxy assigned";
        }
      }
    }

    log("info", "starting parallel account jobs", {
      total: jobs.length,
      concurrency,
      dryRun: options.dryRun,
      marketplace: this.config.marketplace,
    });

    await Promise.all(
      jobs.map((job) =>
        limit(async () => {
          if (job.status === "failed") return;
          await this.executeWithRetries(job, options);
          const delay = randomBetween(
            this.config.delayBetweenJobsMs.min,
            this.config.delayBetweenJobsMs.max,
          );
          await sleep(delay);
        }),
      ),
    );

    const summary = {
      succeeded: jobs.filter((j) => j.status === "succeeded").length,
      failed: jobs.filter((j) => j.status === "failed").length,
      skipped: jobs.filter((j) => j.status === "skipped").length,
    };
    log("info", "run complete", summary);
    return jobs;
  }

  private async executeWithRetries(
    job: Job,
    options: RunOptions,
  ): Promise<void> {
    if (options.skipExisting !== false) {
      const existing =
        this.store.findByIdentityId(job.identity.id) ??
        this.store.findByEmail(job.identity.email);
      if (existing && existing.status !== "dry_run") {
        job.status = "skipped";
        log("info", "skipping identity already in store", {
          identityId: job.identity.id,
          email: job.identity.email,
        });
        return;
      }
    }

    const maxAttempts = this.config.maxRetries + 1;
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      job.attempt = attempt;
      job.status = "running";
      try {
        job.result = await this.executeOnce(job, options.dryRun);
        this.store.upsert(job.result);
        job.status = "succeeded";
        log("info", "job succeeded", {
          jobId: job.id,
          attempt,
          email: job.identity.email,
          status: job.result.status,
        });
        return;
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        job.error = message;
        log("warn", "job attempt failed", {
          jobId: job.id,
          attempt,
          error: message,
        });
        if (attempt >= maxAttempts) {
          job.status = "failed";
          log("error", "job exhausted retries", {
            jobId: job.id,
            email: job.identity.email,
            error: message,
          });
        }
      }
    }
  }

  private async executeOnce(job: Job, dryRun: boolean) {
    const session = await this.browser.openSession({
      identityId: job.identity.id,
      proxy: job.proxy,
      locale: this.config.locale,
      timezone: this.config.timezone,
    });
    try {
      return await this.amazon.register({
        identity: job.identity,
        session,
        proxy: job.proxy,
        mailbox: this.mailbox,
        dryRun,
        config: this.config,
      });
    } finally {
      await session.close();
    }
  }
}

import { createInterface } from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";
import type { OwnedIdentity, SystemConfig } from "../types.js";
import { log, sleep } from "../utils/logger.js";

export interface MailboxAdapter {
  waitForOtp(identity: OwnedIdentity, hint?: string): Promise<string>;
}

export class DryRunMailbox implements MailboxAdapter {
  constructor(private readonly config: SystemConfig["mailbox"]) {}

  async waitForOtp(identity: OwnedIdentity, hint?: string): Promise<string> {
    log("debug", "dry-run mailbox returning fake OTP", {
      email: identity.email,
      hint,
      timeoutMs: this.config.otpTimeoutMs,
    });
    await sleep(Math.min(200, this.config.pollIntervalMs));
    return "000000";
  }
}

/**
 * Reads the OTP from the terminal. Use this when creating one account
 * with an email inbox you can check manually.
 */
export class PromptMailbox implements MailboxAdapter {
  constructor(private readonly config: SystemConfig["mailbox"]) {}

  async waitForOtp(identity: OwnedIdentity, hint?: string): Promise<string> {
    const rl = createInterface({ input, output });
    try {
      const prompt =
        `Enter the Amazon OTP sent to ${identity.email}` +
        (hint ? ` (${hint})` : "") +
        ": ";
      log("info", "waiting for OTP via terminal prompt", {
        email: identity.email,
        timeoutMs: this.config.otpTimeoutMs,
      });

      const timeout = sleep(this.config.otpTimeoutMs).then(() => {
        throw new Error(
          `Timed out waiting for OTP for ${identity.email} after ${this.config.otpTimeoutMs}ms`,
        );
      });

      const answer = await Promise.race([rl.question(prompt), timeout]);
      const otp = String(answer).trim().replace(/\s+/g, "");
      if (!/^\d{4,8}$/.test(otp)) {
        throw new Error(`Invalid OTP format: ${otp}`);
      }
      return otp;
    } finally {
      rl.close();
    }
  }
}

/**
 * Placeholder for IMAP / provider APIs against mailboxes you own.
 */
export class ImapMailbox implements MailboxAdapter {
  constructor(private readonly config: SystemConfig["mailbox"]) {}

  async waitForOtp(identity: OwnedIdentity): Promise<string> {
    void this.config;
    throw new Error(
      `IMAP mailbox adapter is not configured for ${identity.email}. ` +
        "Use --otp-prompt for manual entry, or implement IMAP polling.",
    );
  }
}

export function createMailbox(config: SystemConfig["mailbox"]): MailboxAdapter {
  if (config.provider === "imap") return new ImapMailbox(config);
  if (config.provider === "prompt") return new PromptMailbox(config);
  return new DryRunMailbox(config);
}

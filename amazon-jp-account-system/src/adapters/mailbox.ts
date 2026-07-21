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
 * Placeholder for IMAP / provider APIs against mailboxes you own.
 * Wire your real mail client here; do not use disposable-mail abuse APIs.
 */
export class ImapMailbox implements MailboxAdapter {
  constructor(private readonly config: SystemConfig["mailbox"]) {}

  async waitForOtp(identity: OwnedIdentity): Promise<string> {
    void this.config;
    throw new Error(
      `IMAP mailbox adapter is not configured for ${identity.email}. ` +
        "Implement polling against your own inbox (IMAP_HOST / IMAP_USER / IMAP_PASS).",
    );
  }
}

export function createMailbox(config: SystemConfig["mailbox"]): MailboxAdapter {
  if (config.provider === "imap") return new ImapMailbox(config);
  return new DryRunMailbox(config);
}

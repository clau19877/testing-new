import { createInterface } from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";
import { ImapFlow } from "imapflow";
import { resolveImapSettings } from "../config.js";
import type { ImapSettings, OwnedIdentity, SystemConfig } from "../types.js";
import { log, sleep } from "../utils/logger.js";
import { extractOtpFromText, isAmazonishSender } from "../utils/otp.js";

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
 * Polls an IMAP inbox you own for Amazon OTP messages.
 */
export class ImapMailbox implements MailboxAdapter {
  private readonly imap: ImapSettings;

  constructor(private readonly config: SystemConfig["mailbox"]) {
    this.imap = resolveImapSettings(config.imap);
  }

  async waitForOtp(identity: OwnedIdentity, hint?: string): Promise<string> {
    const startedAt = Date.now();
    const seenUids = new Set<number>();
    log("info", "polling IMAP for Amazon OTP", {
      email: identity.email,
      host: this.imap.host,
      mailbox: this.imap.mailbox,
      hint,
      timeoutMs: this.config.otpTimeoutMs,
      pollIntervalMs: this.config.pollIntervalMs,
    });

    while (Date.now() - startedAt < this.config.otpTimeoutMs) {
      const otp = await this.pollOnce(identity, seenUids, startedAt);
      if (otp) {
        log("info", "OTP found via IMAP", { email: identity.email });
        return otp;
      }
      await sleep(this.config.pollIntervalMs);
    }

    throw new Error(
      `Timed out waiting for IMAP OTP for ${identity.email} after ${this.config.otpTimeoutMs}ms`,
    );
  }

  /** One-shot connectivity check used by `amz-jp test-imap`. */
  async ping(): Promise<{ mailbox: string; exists: number }> {
    const client = this.createClient();
    try {
      await client.connect();
      const lock = await client.getMailboxLock(this.imap.mailbox);
      try {
        const exists = client.mailbox && typeof client.mailbox !== "boolean"
          ? client.mailbox.exists
          : 0;
        return { mailbox: this.imap.mailbox, exists };
      } finally {
        lock.release();
      }
    } finally {
      await client.logout().catch(() => undefined);
    }
  }

  private createClient(): ImapFlow {
    return new ImapFlow({
      host: this.imap.host,
      port: this.imap.port,
      secure: this.imap.secure,
      auth: {
        user: this.imap.user,
        pass: this.imap.pass,
      },
      logger: false,
      tls: {
        rejectUnauthorized: this.imap.tlsRejectUnauthorized,
      },
    });
  }

  private async pollOnce(
    identity: OwnedIdentity,
    seenUids: Set<number>,
    startedAt: number,
  ): Promise<string | undefined> {
    const client = this.createClient();
    try {
      await client.connect();
      const lock = await client.getMailboxLock(this.imap.mailbox);
      try {
        const since = new Date(
          Math.min(startedAt, Date.now()) - this.imap.lookbackMs,
        );
        // Search recent mail; filter Amazon-ish senders in code for provider variance.
        const uids = await client.search({ since }, { uid: true });
        if (!uids || uids.length === 0) return undefined;

        const newestFirst = [...uids].sort((a, b) => b - a).slice(0, 30);
        for (const uid of newestFirst) {
          if (seenUids.has(uid)) continue;
          const otp = await this.readOtpFromUid(client, uid, identity);
          seenUids.add(uid);
          if (otp) return otp;
        }
        return undefined;
      } finally {
        lock.release();
      }
    } finally {
      await client.logout().catch(() => undefined);
    }
  }

  private async readOtpFromUid(
    client: ImapFlow,
    uid: number,
    identity: OwnedIdentity,
  ): Promise<string | undefined> {
    const message = await client.fetchOne(
      String(uid),
      { envelope: true, source: true },
      { uid: true },
    );
    if (!message) return undefined;

    const envelope = message.envelope;
    const from =
      envelope?.from?.map((a) => `${a.name ?? ""} <${a.address ?? ""}>`).join(" ") ??
      "";
    const subject = envelope?.subject ?? "";
    const toAddrs = [
      ...(envelope?.to ?? []),
      ...(envelope?.cc ?? []),
      ...(envelope?.bcc ?? []),
    ]
      .map((a) => (a.address ?? "").toLowerCase())
      .filter(Boolean);

    const identityEmail = identity.email.toLowerCase();
    if (toAddrs.length > 0 && !toAddrs.some((t) => t === identityEmail)) {
      // If the mailbox is shared/catch-all and To: is present, require a match.
      // Still allow when To: is missing (some providers omit it in envelope).
      const userMatches = this.imap.user.toLowerCase() === identityEmail;
      if (!userMatches) return undefined;
    }

    const fromOk =
      this.imap.fromIncludes.length === 0 ||
      this.imap.fromIncludes.some((s) => from.toLowerCase().includes(s.toLowerCase())) ||
      isAmazonishSender(from);
    if (!fromOk) return undefined;

    const subjectOk =
      this.imap.subjectIncludes.length === 0 ||
      this.imap.subjectIncludes.some((s) =>
        subject.toLowerCase().includes(s.toLowerCase()),
      );
    if (!subjectOk) return undefined;

    const source = message.source?.toString("utf8") ?? "";
    const decoded = decodeMailishText(`${subject}\n${from}\n${source}`);
    return extractOtpFromText(decoded);
  }
}

/** Best-effort decode of quoted-printable / simple MIME noise for OTP regexes. */
function decodeMailishText(raw: string): string {
  let text = raw;
  // Strip HTML tags lightly.
  text = text.replace(/<style[\s\S]*?<\/style>/gi, " ");
  text = text.replace(/<script[\s\S]*?<\/script>/gi, " ");
  text = text.replace(/<[^>]+>/g, " ");
  // Quoted-printable soft breaks and hex escapes.
  text = text.replace(/=\r?\n/g, "");
  text = text.replace(/=([0-9A-Fa-f]{2})/g, (_, hex: string) =>
    String.fromCharCode(parseInt(hex, 16)),
  );
  return text.replace(/\s+/g, " ");
}

export function createMailbox(config: SystemConfig["mailbox"]): MailboxAdapter {
  if (config.provider === "imap") return new ImapMailbox(config);
  if (config.provider === "prompt") return new PromptMailbox(config);
  return new DryRunMailbox(config);
}

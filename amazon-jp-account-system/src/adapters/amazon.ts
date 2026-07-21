import type {
  BrowserSession,
  OwnedIdentity,
  ProxyEndpoint,
  RegisterResult,
  SystemConfig,
} from "../types.js";
import type { MailboxAdapter } from "./mailbox.js";
import { log, sleep } from "../utils/logger.js";

export interface AmazonRegisterContext {
  identity: OwnedIdentity;
  session: BrowserSession;
  proxy?: ProxyEndpoint;
  mailbox: MailboxAdapter;
  dryRun: boolean;
  config: SystemConfig;
}

export interface AmazonAdapter {
  /**
   * Register or ensure an amazon.co.jp account for an identity you own.
   * Live implementation should drive your browser session against Amazon's
   * official signup UI using that identity's real email/phone.
   */
  register(ctx: AmazonRegisterContext): Promise<RegisterResult>;
}

export class DryRunAmazonAdapter implements AmazonAdapter {
  async register(ctx: AmazonRegisterContext): Promise<RegisterResult> {
    log("info", "dry-run amazon.co.jp register steps", {
      identityId: ctx.identity.id,
      email: ctx.identity.email,
      profileId: ctx.session.profileId,
      locale: ctx.config.locale,
      timezone: ctx.config.timezone,
    });

    // Simulate realistic step timing without talking to Amazon.
    await sleep(150);
    const otp = await ctx.mailbox.waitForOtp(ctx.identity, "signup");
    await sleep(150);

    return {
      marketplace: ctx.config.marketplace,
      email: ctx.identity.email,
      identityId: ctx.identity.id,
      profileId: ctx.session.profileId,
      proxy: ctx.proxy?.raw,
      status: "dry_run",
      metadata: {
        otpReceived: Boolean(otp),
        steps: [
          "open_register_page",
          "fill_owned_identity",
          "submit_otp",
          "persist_session",
        ],
      },
    };
  }
}

/**
 * Live adapter skeleton. Fill in Playwright/CDP page actions for accounts you own.
 * Intentionally does not ship Amazon anti-bot bypass or disposable-identity flows.
 */
export class LiveAmazonAdapter implements AmazonAdapter {
  async register(ctx: AmazonRegisterContext): Promise<RegisterResult> {
    if (!ctx.session.wsEndpoint) {
      throw new Error(
        "Live Amazon adapter requires a browser session with wsEndpoint (CDP).",
      );
    }
    throw new Error(
      "LiveAmazonAdapter.register is a skeleton. Connect Playwright via " +
        "chromium.connectOverCDP(session.wsEndpoint) and drive amazon.co.jp " +
        "signup for the supplied owned identity.",
    );
  }
}

export function createAmazonAdapter(dryRun: boolean): AmazonAdapter {
  return dryRun ? new DryRunAmazonAdapter() : new LiveAmazonAdapter();
}

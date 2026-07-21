import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import type { Page } from "playwright";
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
  register(ctx: AmazonRegisterContext): Promise<RegisterResult>;
}

const DEFAULT_REGISTER_URL =
  "https://www.amazon.co.jp/ap/register?openid.pape.max_auth_age=0" +
  "&openid.return_to=https%3A%2F%2Fwww.amazon.co.jp%2F%3Fref_%3Dnav_newcust" +
  "&openid.identity=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select" +
  "&openid.assoc_handle=jpflex&openid.mode=checkid_setup" +
  "&openid.claimed_id=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select" +
  "&openid.ns=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0";

export class DryRunAmazonAdapter implements AmazonAdapter {
  async register(ctx: AmazonRegisterContext): Promise<RegisterResult> {
    log("info", "dry-run amazon.co.jp register steps", {
      identityId: ctx.identity.id,
      email: ctx.identity.email,
      profileId: ctx.session.profileId,
      locale: ctx.config.locale,
      timezone: ctx.config.timezone,
    });

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

export class LiveAmazonAdapter implements AmazonAdapter {
  async register(ctx: AmazonRegisterContext): Promise<RegisterResult> {
    const page = await this.resolvePage(ctx.session);
    const steps: string[] = [];
    const registerUrl =
      ctx.config.amazon?.registerUrl ?? DEFAULT_REGISTER_URL;

    try {
      steps.push("open_register_page");
      log("info", "navigating to amazon.co.jp register", { registerUrl });
      await page.goto(registerUrl, { waitUntil: "domcontentloaded", timeout: 60_000 });
      await this.assertNotBlocked(page);

      steps.push("submit_email_claim");
      await this.fillEmailClaim(page, ctx.identity);
      await this.clickContinue(page);
      await page.waitForLoadState("domcontentloaded");
      await sleep(800);
      await this.assertNotBlocked(page);

      if (await this.isExistingAccount(page)) {
        await this.snapshot(page, ctx.identity.id, "already-exists");
        return {
          marketplace: ctx.config.marketplace,
          email: ctx.identity.email,
          identityId: ctx.identity.id,
          profileId: ctx.session.profileId,
          proxy: ctx.proxy?.raw,
          status: "already_exists",
          metadata: { steps, finalUrl: page.url() },
        };
      }

      if (await this.isIntentConfirmation(page)) {
        steps.push("confirm_create_intent");
        await this.clickContinue(page);
        await page.waitForLoadState("domcontentloaded");
        await sleep(800);
        await this.assertNotBlocked(page);
      }

      steps.push("fill_name_password");
      await this.fillCreateAccountForm(page, ctx.identity);
      await this.clickCreateAccount(page);
      await page.waitForLoadState("domcontentloaded");
      await sleep(1000);

      if (await this.isChallengePage(page)) {
        steps.push("manual_challenge");
        await this.waitForManualChallenge(page, ctx);
      }

      if (await this.isOtpPage(page)) {
        steps.push("submit_otp");
        const otp = await ctx.mailbox.waitForOtp(ctx.identity, "amazon signup");
        await this.fillOtp(page, otp);
        await this.clickContinue(page);
        await page.waitForLoadState("domcontentloaded");
        await sleep(1500);
      }

      if (await this.isChallengePage(page)) {
        steps.push("manual_challenge_post_otp");
        await this.waitForManualChallenge(page, ctx);
      }

      await this.assertNoAuthError(page);

      const loggedIn = await this.isLoggedIn(page);
      if (!loggedIn && !(await this.looksLikeSuccess(page))) {
        await this.snapshot(page, ctx.identity.id, "uncertain");
        throw new Error(
          `Registration did not reach a clear success state. url=${page.url()}`,
        );
      }

      steps.push("persist_session");
      await this.snapshot(page, ctx.identity.id, "success");

      return {
        marketplace: ctx.config.marketplace,
        email: ctx.identity.email,
        identityId: ctx.identity.id,
        profileId: ctx.session.profileId,
        proxy: ctx.proxy?.raw,
        status: "created",
        metadata: {
          steps,
          finalUrl: page.url(),
          loggedIn,
        },
      };
    } catch (err) {
      await this.snapshot(page, ctx.identity.id, "error").catch(() => undefined);
      throw err;
    }
  }

  private async resolvePage(session: BrowserSession): Promise<Page> {
    if (session.page) return session.page;
    if (session.wsEndpoint) {
      const { chromium } = await import("playwright");
      const browser = await chromium.connectOverCDP(session.wsEndpoint);
      const context = browser.contexts()[0] ?? (await browser.newContext());
      const page = context.pages()[0] ?? (await context.newPage());
      return page;
    }
    throw new Error(
      "Live Amazon adapter needs a Playwright page or CDP wsEndpoint. " +
        "Use browser.provider=playwright (recommended for create-one).",
    );
  }

  private async fillEmailClaim(page: Page, identity: OwnedIdentity): Promise<void> {
    const emailSelectors = [
      "#ap_email_login",
      "#ap_email",
      'input[name="email"]',
      'input[type="email"]',
      'input[name="email"]',
    ];
    const emailInput = await this.firstVisible(page, emailSelectors);
    if (!emailInput) {
      throw new Error("Could not find email/phone input on Amazon register page");
    }
    await emailInput.click({ clickCount: 3 });
    await emailInput.fill(identity.email);
    log("info", "filled email claim", { email: identity.email });
  }

  private async fillCreateAccountForm(
    page: Page,
    identity: OwnedIdentity,
  ): Promise<void> {
    // Classic register form (name + email + password) OR continuation create form.
    const nameInput = await this.firstVisible(page, [
      "#ap_customer_name",
      'input[name="customerName"]',
      "#ap_customer_name_input",
    ]);
    if (nameInput) {
      await nameInput.fill(identity.fullName);
    }

    const emailInput = await this.firstVisible(page, [
      "#ap_email",
      'input[name="email"]',
      "#ap_email_login",
    ]);
    if (emailInput) {
      const current = await emailInput.inputValue().catch(() => "");
      if (!current) await emailInput.fill(identity.email);
    }

    const passwordInput = await this.firstVisible(page, [
      "#ap_password",
      'input[name="password"]',
      'input[type="password"]',
    ]);
    if (!passwordInput) {
      // Maybe still on claim page or unexpected UI.
      const body = (await page.locator("body").innerText().catch(() => "")).slice(0, 400);
      throw new Error(
        `Could not find password field after email claim. url=${page.url()} body=${body}`,
      );
    }
    await passwordInput.fill(identity.password);

    const passwordCheck = await this.firstVisible(page, [
      "#ap_password_check",
      'input[name="passwordCheck"]',
    ]);
    if (passwordCheck) {
      await passwordCheck.fill(identity.password);
    }

    log("info", "filled name/password create form", {
      hasName: Boolean(nameInput),
      hasPasswordCheck: Boolean(passwordCheck),
    });
  }

  private async fillOtp(page: Page, otp: string): Promise<void> {
    const otpInput = await this.firstVisible(page, [
      "#cvf-input-code",
      'input[name="code"]',
      'input[name="otpCode"]',
      "#auth-mfa-otpcode",
      'input[type="tel"]',
    ]);
    if (!otpInput) {
      throw new Error(`OTP input not found. url=${page.url()}`);
    }
    await otpInput.fill(otp);
    log("info", "filled OTP");
  }

  private async clickContinue(page: Page): Promise<void> {
    // Prefer labeled continue/create buttons, then generic visible submit.
    const candidates = [
      "#continue",
      'input#continue',
      'span.a-button-inner input[type="submit"]',
      'input.a-button-input[type="submit"]',
      'input[type="submit"]',
      'button[type="submit"]',
    ];
    for (const selector of candidates) {
      const loc = page.locator(selector).first();
      if (await loc.isVisible().catch(() => false)) {
        await loc.click();
        return;
      }
    }
    await page.keyboard.press("Enter");
  }

  private async clickCreateAccount(page: Page): Promise<void> {
    const selectors = [
      "#continue",
      "#auth-continue",
      'input#continue',
      '#register_button',
      'input[type="submit"]',
      'button[type="submit"]',
    ];
    const btn = await this.firstVisible(page, selectors);
    if (!btn) {
      await page.keyboard.press("Enter");
      return;
    }
    await btn.click();
  }

  private async isChallengePage(page: Page): Promise<boolean> {
    const url = page.url();
    if (url.includes("/ap/cvf/") || url.includes("validateCaptcha")) return true;
    const text = await page.locator("body").innerText().catch(() => "");
    return /パズル|クイズを開始|ロボット|captcha|Enter the characters|この画像に見える文字|アカウント保護のため/i.test(
      text,
    );
  }

  /**
   * Amazon often shows a CVF puzzle after form submit.
   * In headed mode we pause so you can solve it in the browser;
   * we resume automatically once OTP/success UI appears.
   */
  private async waitForManualChallenge(
    page: Page,
    ctx: AmazonRegisterContext,
  ): Promise<void> {
    await this.snapshot(page, ctx.identity.id, "challenge");
    if (ctx.config.browser.headless) {
      throw new Error(
        "Amazon puzzle/captcha detected. Re-run with headed mode " +
          "(`npm run create-one -- --headed ...`) and solve the puzzle in the browser.",
      );
    }

    // Try to open the puzzle UI if the start button is present.
    const start = page.getByRole("button", { name: /クイズを開始|Start/i });
    if (await start.isVisible().catch(() => false)) {
      await start.click().catch(() => undefined);
    }

    const timeoutMs = Math.max(ctx.config.mailbox.otpTimeoutMs, 180_000);
    log("info", "Amazon challenge detected — solve it in the browser window", {
      url: page.url(),
      timeoutMs,
    });

    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (await this.isOtpPage(page)) return;
      if (await this.isLoggedIn(page)) return;
      if (await this.looksLikeSuccess(page)) return;
      if (!(await this.isChallengePage(page))) {
        // Challenge cleared; give the next page a moment to settle.
        await sleep(800);
        return;
      }
      await sleep(1500);
    }
    throw new Error(
      `Timed out waiting for Amazon challenge to be solved (${timeoutMs}ms)`,
    );
  }

  private async isIntentConfirmation(page: Page): Promise<boolean> {
    const url = page.url();
    if (url.includes("/ax/claim/intent")) return true;
    const text = await page.locator("body").innerText().catch(() => "");
    return /初めてご利用|Looks like you're new|アカウントの作成に進む|create an account/i.test(
      text,
    );
  }

  private async isExistingAccount(page: Page): Promise<boolean> {
    const url = page.url();
    const text = await page.locator("body").innerText().catch(() => "");

    // Intent confirmation means new user — not existing.
    if (await this.isIntentConfirmation(page)) return false;

    if (url.includes("/ap/signin") || /サインイン|Sign in/i.test(text)) {
      const hasCreateName = await this.firstVisible(page, [
        "#ap_customer_name",
        'input[name="customerName"]',
      ]);
      const passwordOnly = await this.firstVisible(page, [
        "#ap_password",
        'input[name="password"]',
      ]);
      if (!hasCreateName && passwordOnly) return true;
    }

    const alert = page.locator("#auth-error-message-box, .a-alert-content");
    if (await alert.first().isVisible().catch(() => false)) {
      const msg = await alert.first().innerText().catch(() => "");
      if (/already|既に|存在する|登録済/i.test(msg)) return true;
    }
    return false;
  }

  private async isOtpPage(page: Page): Promise<boolean> {
    if (
      await this.firstVisible(page, [
        "#cvf-input-code",
        'input[name="code"]',
        'input[name="otpCode"]',
        "#auth-mfa-otpcode",
      ])
    ) {
      return true;
    }
    const text = await page.locator("body").innerText().catch(() => "");
    return /ワンタイム|OTP|verification code|認証コード|セキュリティコード/i.test(
      text,
    );
  }

  private async isLoggedIn(page: Page): Promise<boolean> {
    const nav = page.locator(
      "#nav-link-accountList, #nav-item-signout, #nav-greeting-name",
    );
    if (await nav.first().isVisible().catch(() => false)) {
      const text = await nav.first().innerText().catch(() => "");
      if (/こんにちは|Hello|Account|アカウント/i.test(text)) return true;
    }
    const url = page.url();
    return (
      url.includes("/gp/yourstore") ||
      (url.includes("amazon.co.jp") && !url.includes("/ap/") && !url.includes("cvf"))
    );
  }

  private async looksLikeSuccess(page: Page): Promise<boolean> {
    const url = page.url();
    if (url.includes("amazon.co.jp") && !url.includes("/ap/") && !url.includes("cvf")) {
      return true;
    }
    const text = await page.locator("body").innerText().catch(() => "");
    return /アカウントが作成|Account created|登録が完了/i.test(text);
  }

  private async assertNotBlocked(page: Page): Promise<void> {
    const url = page.url();
    const text = await page.locator("body").innerText().catch(() => "");
    if (
      /ロボット|captcha|Enter the characters|この画像に見える文字/i.test(text) ||
      url.includes("/errors/validateCaptcha")
    ) {
      throw new Error(
        "Amazon captcha/challenge detected. Re-run with --headed and solve it manually, then retry.",
      );
    }
  }

  private async assertNoAuthError(page: Page): Promise<void> {
    const box = page.locator("#auth-error-message-box, .a-alert-error");
    if (await box.first().isVisible().catch(() => false)) {
      const msg = (await box.first().innerText().catch(() => "")).trim();
      if (msg) throw new Error(`Amazon auth error: ${msg}`);
    }
  }

  private async firstVisible(page: Page, selectors: string[]) {
    for (const selector of selectors) {
      const loc = page.locator(selector).first();
      if (await loc.isVisible().catch(() => false)) return loc;
    }
    return null;
  }

  private async snapshot(
    page: Page,
    identityId: string,
    label: string,
  ): Promise<string> {
    const file = resolve(
      process.cwd(),
      "data",
      "artifacts",
      `${identityId}-${label}-${Date.now()}.png`,
    );
    mkdirSync(dirname(file), { recursive: true });
    await page.screenshot({ path: file, fullPage: true });
    log("info", "saved screenshot", { file });
    return file;
  }
}

export function createAmazonAdapter(dryRun: boolean): AmazonAdapter {
  return dryRun ? new DryRunAmazonAdapter() : new LiveAmazonAdapter();
}

import { chromium, type Browser, type Page } from "playwright";
import type { BrowserSession, ProxyEndpoint, SystemConfig } from "../types.js";
import { log } from "../utils/logger.js";

export interface BrowserAdapter {
  openSession(input: {
    identityId: string;
    proxy?: ProxyEndpoint;
    locale: string;
    timezone: string;
  }): Promise<BrowserSession>;
}

export class DryRunBrowser implements BrowserAdapter {
  constructor(private readonly config: SystemConfig["browser"]) {}

  async openSession(input: {
    identityId: string;
    proxy?: ProxyEndpoint;
    locale: string;
    timezone: string;
  }): Promise<BrowserSession> {
    const profileId = `dry-${input.identityId}`;
    log("info", "opened dry-run browser profile", {
      profileId,
      locale: input.locale,
      timezone: input.timezone,
      proxy: input.proxy?.server,
      headless: this.config.headless,
    });
    return {
      profileId,
      close: async () => {
        log("debug", "closed dry-run browser profile", { profileId });
      },
    };
  }
}

export class PlaywrightBrowser implements BrowserAdapter {
  constructor(private readonly config: SystemConfig["browser"]) {}

  async openSession(input: {
    identityId: string;
    proxy?: ProxyEndpoint;
    locale: string;
    timezone: string;
  }): Promise<BrowserSession> {
    const profileId = `pw-${input.identityId}-${Date.now()}`;
    const launchOptions: Parameters<typeof chromium.launch>[0] = {
      headless: this.config.headless,
      slowMo: this.config.slowMoMs ?? 50,
      args: ["--disable-blink-features=AutomationControlled"],
    };

    if (input.proxy) {
      launchOptions.proxy = {
        server: input.proxy.server,
        username: input.proxy.username,
        password: input.proxy.password,
      };
    }

    const browser: Browser = await chromium.launch(launchOptions);
    const context = await browser.newContext({
      locale: input.locale,
      timezoneId: input.timezone,
      viewport: { width: 1280, height: 900 },
      userAgent:
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    });
    const page: Page = await context.newPage();

    log("info", "opened playwright browser session", {
      profileId,
      headless: this.config.headless,
      locale: input.locale,
      timezone: input.timezone,
      proxy: input.proxy?.server,
    });

    return {
      profileId,
      page,
      close: async () => {
        await context.close().catch(() => undefined);
        await browser.close().catch(() => undefined);
        log("debug", "closed playwright browser session", { profileId });
      },
    };
  }
}

/**
 * Stub for AdsPower / GoLogin / Multilogin CDP attach.
 */
export class CdpBrowser implements BrowserAdapter {
  constructor(private readonly config: SystemConfig["browser"]) {}

  async openSession(input: {
    identityId: string;
    proxy?: ProxyEndpoint;
  }): Promise<BrowserSession> {
    void this.config;
    void input;
    throw new Error(
      "CDP browser adapter not wired yet. Point this at your antidetect profile API " +
        "(AdsPower/GoLogin/Multilogin) and return connectOverCDP wsEndpoint.",
    );
  }
}

export function createBrowser(config: SystemConfig["browser"]): BrowserAdapter {
  if (config.provider === "playwright") return new PlaywrightBrowser(config);
  if (config.provider === "cdp") return new CdpBrowser(config);
  return new DryRunBrowser(config);
}

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

/**
 * Stub for AdsPower / GoLogin / Multilogin CDP attach.
 * Implement provider-specific start-profile → wsEndpoint here.
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
  if (config.provider === "cdp") return new CdpBrowser(config);
  return new DryRunBrowser(config);
}

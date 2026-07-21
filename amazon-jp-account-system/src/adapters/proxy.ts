import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import type { ProxyEndpoint } from "../types.js";

export function parseProxyLine(line: string): ProxyEndpoint {
  const trimmed = line.trim();
  if (!trimmed || trimmed.startsWith("#")) {
    throw new Error("empty proxy line");
  }

  if (trimmed.includes("://")) {
    const url = new URL(trimmed);
    const server = `${url.protocol}//${url.hostname}:${url.port || "80"}`;
    return {
      raw: trimmed,
      server,
      username: url.username || undefined,
      password: url.password || undefined,
    };
  }

  const parts = trimmed.split(":");
  if (parts.length === 2) {
    return { raw: trimmed, server: `http://${parts[0]}:${parts[1]}` };
  }
  if (parts.length === 4) {
    return {
      raw: trimmed,
      server: `http://${parts[0]}:${parts[1]}`,
      username: parts[2],
      password: parts[3],
    };
  }
  throw new Error(`Unsupported proxy format: ${trimmed}`);
}

export function loadProxies(path?: string): ProxyEndpoint[] {
  const file = resolve(
    process.cwd(),
    path ?? process.env.PROXIES_PATH ?? "config/proxies.example.txt",
  );
  if (!existsSync(file)) {
    return [];
  }
  const lines = readFileSync(file, "utf8").split(/\r?\n/);
  const proxies: ProxyEndpoint[] = [];
  for (const line of lines) {
    if (!line.trim() || line.trim().startsWith("#")) continue;
    proxies.push(parseProxyLine(line));
  }
  return proxies;
}

/** Sticky assignment: identity index maps to proxy index (round-robin if fewer proxies). */
export function assignStickyProxy(
  proxies: ProxyEndpoint[],
  index: number,
): ProxyEndpoint | undefined {
  if (proxies.length === 0) return undefined;
  return proxies[index % proxies.length];
}

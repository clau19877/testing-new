export function log(
  level: "info" | "warn" | "error" | "debug",
  message: string,
  meta?: Record<string, unknown>,
): void {
  const line = {
    ts: new Date().toISOString(),
    level,
    message,
    ...meta,
  };
  const text = JSON.stringify(line);
  if (level === "error") {
    console.error(text);
  } else if (level === "warn") {
    console.warn(text);
  } else {
    console.log(text);
  }
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function randomBetween(min: number, max: number): number {
  if (max <= min) return min;
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

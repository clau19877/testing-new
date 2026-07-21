/**
 * Extract a one-time passcode from Amazon (or similar) email bodies/subjects.
 * Prefers 6-digit codes; falls back to 4–8 digit codes near OTP keywords.
 */
export function extractOtpFromText(text: string): string | undefined {
  const normalized = text.replace(/\r/g, "\n");

  const keywordNear = normalized.match(
    /(?:OTP|one[-\s]?time|verification|security|認証|確認|ワンタイム|セキュリティ)[^\d]{0,40}(\d{4,8})/i,
  );
  if (keywordNear?.[1]) return keywordNear[1];

  const reverseNear = normalized.match(
    /(\d{4,8})[^\d]{0,40}(?:OTP|one[-\s]?time|verification|認証|確認|ワンタイム)/i,
  );
  if (reverseNear?.[1]) return reverseNear[1];

  const six = normalized.match(/(?<![0-9])(\d{6})(?![0-9])/);
  if (six?.[1]) return six[1];

  return undefined;
}

export function isAmazonishSender(from: string): boolean {
  const f = from.toLowerCase();
  return (
    f.includes("amazon.") ||
    f.includes("amazon.co.jp") ||
    f.includes("account-update@") ||
    f.includes("auto-confirm@") ||
    f.includes("@amazon")
  );
}

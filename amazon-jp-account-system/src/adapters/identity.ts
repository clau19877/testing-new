import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { z } from "zod";
import type { OwnedIdentity } from "../types.js";

const IdentitySchema = z.object({
  id: z.string().min(1),
  email: z.string().email(),
  password: z.string().min(8),
  fullName: z.string().min(1),
  phoneE164: z.string().optional(),
  postalCode: z.string().optional(),
  prefecture: z.string().optional(),
  city: z.string().optional(),
  addressLine1: z.string().optional(),
  notes: z.string().optional(),
});

/**
 * Loads identities you own from a JSON file.
 * This system does not invent or forge identity documents.
 */
export function loadOwnedIdentities(path?: string): OwnedIdentity[] {
  const file = resolve(
    process.cwd(),
    path ?? process.env.IDENTITIES_PATH ?? "config/identities.example.json",
  );
  const raw = JSON.parse(readFileSync(file, "utf8")) as unknown;
  const list = z.array(IdentitySchema).parse(raw);
  if (list.length === 0) {
    throw new Error(`No identities found in ${file}`);
  }
  return list;
}

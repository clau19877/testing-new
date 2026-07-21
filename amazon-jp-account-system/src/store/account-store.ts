import { mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import type { AccountRecord, RegisterResult } from "../types.js";

export class AccountStore {
  private readonly path: string;
  private records: AccountRecord[] = [];

  constructor(storePath: string) {
    this.path = resolve(process.cwd(), storePath);
    this.load();
  }

  private load(): void {
    if (!existsSync(this.path)) {
      this.records = [];
      return;
    }
    const raw = JSON.parse(readFileSync(this.path, "utf8")) as AccountRecord[];
    this.records = Array.isArray(raw) ? raw : [];
  }

  private persist(): void {
    mkdirSync(dirname(this.path), { recursive: true });
    writeFileSync(this.path, JSON.stringify(this.records, null, 2) + "\n");
  }

  list(): AccountRecord[] {
    return [...this.records];
  }

  findByIdentityId(identityId: string): AccountRecord | undefined {
    return this.records.find((r) => r.identityId === identityId);
  }

  findByEmail(email: string): AccountRecord | undefined {
    return this.records.find(
      (r) => r.email.toLowerCase() === email.toLowerCase(),
    );
  }

  upsert(result: RegisterResult, error?: string): AccountRecord {
    const now = new Date().toISOString();
    const existingIdx = this.records.findIndex(
      (r) => r.identityId === result.identityId,
    );
    const record: AccountRecord = {
      ...result,
      createdAt:
        existingIdx >= 0 ? this.records[existingIdx].createdAt : now,
      updatedAt: now,
      lastError: error,
    };
    if (existingIdx >= 0) {
      this.records[existingIdx] = record;
    } else {
      this.records.push(record);
    }
    this.persist();
    return record;
  }
}

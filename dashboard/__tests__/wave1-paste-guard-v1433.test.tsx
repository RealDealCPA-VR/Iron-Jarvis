/**
 * v1.433.0 (Wave 1 B, A4, the dashboard half): the composer's paste guard
 * holds back two more kinds of secret.
 *
 * `looksLikeSecret` (lib/configCards) holds a message that looks like a key
 * and offers the secure card instead of sending it into the chat. It missed:
 *  - a Discord webhook address — the address IS the secret (anyone holding
 *    it can post as the hook);
 *  - a database connection string that carries a password
 *    (postgres://user:pass@host, mysql://user:pass@host).
 * Ordinary addresses, and a connection string with no password, still send.
 */
import { describe, expect, it } from "vitest";
import { looksLikeSecret } from "@/lib/configCards";

const HOOK_TOKEN = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-abcdefghijklmnopqrstuvwxyzABCD";

describe("held back", () => {
  it.each([
    `https://discord.com/api/webhooks/123456789012345678/${HOOK_TOKEN}`,
    `post this to https://discordapp.com/api/webhooks/123456789012345678/${HOOK_TOKEN} please`,
    `https://ptb.discord.com/api/webhooks/987654321098765432/${HOOK_TOKEN}`,
    `https://canary.discord.com/api/v10/webhooks/987654321098765432/${HOOK_TOKEN}`,
    "postgres://ledger:s3cret-pw@db.example.com:5432/books",
    "connect with postgresql://admin:hunter2@localhost/clients",
    "postgresql+psycopg2://app:pa55word@10.0.0.5/ledger",
    "mysql://root:toor@127.0.0.1:3306/payroll",
    "MYSQL://Root:Toor@db/payroll",
    "postgres://:onlypass@db.local/x",
  ])("%s", (text) => {
    expect(looksLikeSecret(text)).toBe(true);
  });
});

describe("held back too (v1.433.0 review)", () => {
  // The first cut missed these: the www. host and an address pasted without
  // its https://, the other common databases (MongoDB Atlas, MariaDB, Redis,
  // SQL Server), and a password holding a "/".
  it.each([
    `https://www.discord.com/api/webhooks/123456789012345678/${HOOK_TOKEN}`,
    `discord.com/api/webhooks/123456789012345678/${HOOK_TOKEN}`,
    `canary.discordapp.com/api/webhooks/123456789012345678/${HOOK_TOKEN}`,
    "mongodb+srv://ledger:s3cret@cluster0.ab12c.mongodb.net/books",
    "mongodb://admin:hunter2@10.0.0.7:27017/clients?authSource=admin",
    "mariadb://root:toor@db.local/payroll",
    "redis://:s3cretpw@cache.local:6379/0",
    "rediss://default:s3cretpw@cache.example.com:6380",
    "mssql://sa:Str0ng!pw@sql.local/books",
    "postgresql+psycopg://app:ab/cd+ef==@db.local/ledger",
    "postgres://app:p@ss@db.local/ledger",
  ])("%s", (text) => {
    expect(looksLikeSecret(text)).toBe(true);
  });
});

describe("not held back", () => {
  it.each([
    "https://discord.com/channels/123456789012345678/987654321098765432",
    "https://discord.com/api/webhooks",
    "https://example.com/api/webhooks/123/abc",
    "https://www.irs.gov/forms-pubs/about-form-1040",
    "postgres://db.example.com:5432/books",
    "postgres://ledger@db.example.com/books",
    "mysql://root@localhost/payroll",
    "postgresql://localhost/clients",
    "the postgres server at db.local:5432 is slow",
    "email me at vr@example.com",
    "https://user@example.com/path",
    // v1.433.0 review: the wider shapes still let these through.
    "mongodb://db.local:27017/books",
    "redis://cache.local:6379/0",
    "postgres://db.local:5432/books?sslmode=require&user=me@work",
    "http://localhost:4873/@myorg/pkg",
    "https://mydiscord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz",
  ])("%s", (text) => {
    expect(looksLikeSecret(text)).toBe(false);
  });

  it("the older shapes still hold", () => {
    expect(looksLikeSecret("ghp_abcdefghijklmnopqrstuvwxyz0123")).toBe(true);
    expect(looksLikeSecret("Bearer of bad news")).toBe(false);
  });
});

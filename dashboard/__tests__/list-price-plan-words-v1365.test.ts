/**
 * IJ harness phase 2 review — codex-cli (the user's ChatGPT plan) is a
 * list-price EQUIVALENT too (eval/pricing LIST_PRICE_EQUIVALENT_PROVIDERS),
 * so a surface that explains a list-price figure must not call it the
 * "Claude subscription": the same words now sit beside Codex work.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { LIST_PRICE_TITLE } from "@/lib/types";

const ROOT = path.resolve(__dirname, "..");

/** CRLF-normalised at the reader (the v1.232.1 rule). */
function source(rel: string): string {
  return readFileSync(path.join(ROOT, rel), "utf-8").replace(/\r\n/g, "\n");
}

describe("list-price words name no one vendor's plan", () => {
  it("the shared title says subscription, not Claude", () => {
    expect(LIST_PRICE_TITLE).toContain("subscription");
    expect(LIST_PRICE_TITLE).not.toMatch(/Claude|ChatGPT/);
  });

  it.each(["app/activity/page.tsx", "components/TimeTravelFeed.tsx"])(
    "%s explains a list-price figure without 'Claude subscription'",
    (rel) => {
      expect(source(rel)).not.toContain("Claude subscription");
    },
  );
});

/**
 * Independent review of lane O (v1.420.0, C6.3 and C6.8), dashboard half.
 *
 * - The Grok sign-in press must not link a page that installs a DIFFERENT
 *   Grok: the app drives xAI's own CLI (~/.grok/bin/grok.exe, auth.json),
 *   and github.com/superagent-ai/grok-cli is a key-based CLI with no sign-in.
 *   No page is known to be right, so none is linked (RED on the first cut).
 * - `waiting_remote` (a remote took the job and reports back later) has its
 *   own word in the ONE status table, never the amber "Needs you" and never
 *   shown as the raw id.
 */
import { describe, expect, it } from "vitest";

import { GROK_SIGN_IN, subscriptionCliFor } from "@/lib/cliSignIn";
import { statusWords } from "@/lib/statusWords";

describe("lane O review", () => {
  it("the Grok press links no install page it cannot vouch for", () => {
    expect(GROK_SIGN_IN.installUrl ?? "").not.toMatch(/superagent/i);
    expect(GROK_SIGN_IN.installUrl).toBeUndefined();
    expect(GROK_SIGN_IN.installLabel).toBeUndefined();
    // Claude Code and Codex keep theirs.
    expect(subscriptionCliFor("claude-cli")?.installUrl).toMatch(/^https:\/\//);
    expect(subscriptionCliFor("codex-cli")?.installUrl).toMatch(/^https:\/\//);
  });

  it("a remote that accepted a job reads as waiting on the remote, not on the user", () => {
    const s = statusWords("waiting_remote");
    expect(s.word).toBe("Will report back");
    expect(s.tone).toBe("cyan");
    expect(s.word).not.toBe(statusWords("waiting_you").word);
  });
});

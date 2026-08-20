/**
 * Chat consent: the approval posture and the card's conversation grant.
 *
 * Three defects, one theme — consent that either leaked where it was never
 * given, or evaporated where it was:
 *
 *  05  `newChat()` reset workspace/persona/tools/connectors/skill but NOT the
 *      approval posture, so opening a saved YOLO thread and clicking New chat
 *      left the fresh conversation auto-approving the whole ask tier.
 *      `openThread()` had always done this reset; New chat had not.
 *  18  `sendAgent` built `allow_tools` from the `selectedTools` binding of the
 *      render where the turn STARTED, so a tool granted mid-turn on the
 *      approval card was absent from that same turn's escalation body —
 *      contradicting the v1.187.0 "grants RIDE ESCALATIONS" contract written
 *      in the comment directly above it.
 *  39  The card's `onConversation` armed the tool without `markSetupChanged()`,
 *      so on a never-otherwise-marked thread `queueSave` sent `setup: null`
 *      forever and the grant was gone on reopen.
 *
 * The chat page is never rendered in any suite (it is the app's largest
 * component), so this follows the house idiom for page-level seams and pins
 * the SOURCE — see approval-mode-v1188.test.tsx and the draftFromFence
 * (v1.163.0) lesson. Bodies are extracted by brace matching so a pin says
 * "inside THIS function", which is the whole content of findings 05 and 18.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const page = readFileSync(join(process.cwd(), "app", "chat", "page.tsx"), "utf8");

/** The `{...}` body of a top-level-in-component function declaration. */
function bodyOf(name: string): string {
  const decl = new RegExp(`function ${name}\\b`).exec(page);
  if (!decl) throw new Error(`function ${name}() not found in app/chat/page.tsx`);
  // Step over the PARAMETER list first — sendAgent's options parameter is an
  // inline object type, whose `{` would otherwise be mistaken for the body.
  let paren = 0;
  let afterParams = -1;
  for (let i = page.indexOf("(", decl.index); i < page.length; i++) {
    if (page[i] === "(") paren += 1;
    else if (page[i] === ")") {
      paren -= 1;
      if (paren === 0) {
        afterParams = i;
        break;
      }
    }
  }
  if (afterParams < 0) throw new Error(`unbalanced parameters for ${name}()`);
  const open = page.indexOf("{", afterParams);
  let depth = 0;
  for (let i = open; i < page.length; i++) {
    if (page[i] === "{") depth += 1;
    else if (page[i] === "}") {
      depth -= 1;
      if (depth === 0) return page.slice(open, i + 1);
    }
  }
  throw new Error(`unbalanced body for ${name}()`);
}

describe("the extractor itself", () => {
  it("finds real, bounded bodies", () => {
    // If this ever drifts, every pin below becomes meaningless rather than red.
    const nc = bodyOf("newChat");
    expect(nc.startsWith("{")).toBe(true);
    expect(nc.endsWith("}")).toBe(true);
    expect(nc).toContain("chatGenRef.current += 1");
    expect(nc).not.toContain("function sendAgent");
    // The parameter-list skip: sendAgent's body, not its options type.
    const sa = bodyOf("sendAgent");
    expect(sa).toContain("await post<SessionView>(\"/sessions\"");
    expect(sa).toContain('origin: "chat"');
    expect(sa).not.toContain("function newChat");
  });
});

describe("finding 05 — New chat returns to the DEFAULT posture", () => {
  const body = bodyOf("newChat");

  it("re-reads the stored default instead of carrying the thread's posture", () => {
    expect(body).toContain("setApprovalMode(");
    expect(body).toContain("localStorage.getItem(APPROVAL_MODE_KEY)");
    expect(body).toMatch(
      /setApprovalMode\(asApprovalMode\(localStorage\.getItem\(APPROVAL_MODE_KEY\)\)\)/,
    );
  });

  it("falls back to approve_for_me — never to yolo — if storage throws", () => {
    // Fail closed: an unreadable localStorage must not leave the previous
    // thread's grant standing, and must not invent one.
    expect(body).toMatch(/catch[\s\S]{0,120}setApprovalMode\("approve_for_me"\)/);
    expect(body).not.toContain('"yolo"');
  });

  it("still clears the rest of the per-conversation consent", () => {
    // The posture reset joins these; it must not have displaced any of them.
    expect(body).toContain("setSelectedTools(");
    expect(body).toContain("setSelectedConnectors([])");
    expect(body).toContain("sendSetupRef.current = false");
  });

  it("is the same reset openThread performs (one contract, two doors)", () => {
    const open = page.indexOf("setSelectedTools([]); // armed tools are per-conversation");
    expect(open).toBeGreaterThan(-1);
    expect(page.slice(open, open + 700)).toContain(
      "localStorage.getItem(APPROVAL_MODE_KEY)",
    );
  });
});

describe("finding 18 — a mid-turn grant rides a SAME-turn escalation", () => {
  it("mirrors the armed set into a ref on every render", () => {
    expect(page).toMatch(
      /const selectedToolsRef = useRef<string\[\]>\(selectedTools\);\s*\n\s*selectedToolsRef\.current = selectedTools;/,
    );
  });

  it("builds allow_tools from the ref in BOTH escalation branches", () => {
    const body = bodyOf("sendAgent");
    // The custom-agent /agents/{slug}/spawn branch and the POST /sessions one.
    const spreads = body.match(/allow_tools: (\w+)\.slice\(0, MAX_TOOLS\)/g) ?? [];
    expect(spreads).toHaveLength(2);
    for (const s of spreads) expect(s).toContain("armedNow");
    // The stale-closure binding must not be what a grant is measured against.
    expect(body).not.toMatch(/allow_tools: selectedTools/);
    expect(body).not.toMatch(/selectedTools\.length\s*\n?\s*\?\s*\{ allow_tools/);
    expect(body).toContain("const armedNow = selectedToolsRef.current;");
  });
});

describe("finding 39 — the grant is real consent: it persists", () => {
  const body = bodyOf("armFromApproval");

  it("writes the ref synchronously, arms state, then marks the setup changed", () => {
    // Order matters: the ref write is what a same-turn escalation reads before
    // React has re-rendered; markSetupChanged is what makes the save carry it.
    expect(body).toMatch(
      /selectedToolsRef\.current = \[\.\.\.prev, tool\][\s\S]{0,200}setSelectedTools\([\s\S]{0,200}markSetupChanged\(\)/,
    );
    // Still bounded by the same cap and still idempotent.
    expect(body).toContain("prev.includes(tool) || prev.length >= MAX_TOOLS");
  });

  it("marks the setup through the guard the save actually consults", () => {
    expect(bodyOf("markSetupChanged")).toContain("sendSetupRef.current = true");
    // ...and queueSave only attaches a setup when that guard is set.
    expect(bodyOf("queueSave")).toContain(
      "const setup = sendSetupRef.current ? currentSetup() : null;",
    );
  });

  it("snapshots the armed set from the ref, so the turn's OWN save carries it", () => {
    // queueSave runs at turn completion inside the send's stale closure; the
    // grant made during that turn must ride that first save or a fresh thread
    // is stored without it and reopens unarmed.
    expect(bodyOf("currentSetup")).toContain(
      "tools: selectedToolsRef.current.slice(0, MAX_TOOLS)",
    );
  });

  it("routes BOTH cards (chat's mid-turn ask and the run's mid-run ask) here", () => {
    expect(page.match(/onConversation=\{armFromApproval\}/g) ?? []).toHaveLength(2);
    // No hand-rolled inline handler may survive alongside it — that is exactly
    // how the two lanes drifted apart in the first place.
    expect(page).not.toMatch(/onConversation=\{\(tool\)/);
  });
});

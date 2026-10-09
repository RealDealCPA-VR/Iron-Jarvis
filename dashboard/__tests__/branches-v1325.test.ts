/**
 * v1.325.0 — wave D: BRANCHES. Editing a question or asking for another
 * answer keeps the old ending as a stored version on the FIRST message of the
 * live tail (`branch.tails`, `null` = the live slot). lib/branches is pure;
 * these tests pin the shape, the round trips, nested forks and the cap.
 */

import { describe, expect, it } from "vitest";
import {
  MAX_BRANCHES,
  branchInfo,
  forkOnto,
  forkTail,
  stripBranches,
  switchBranch,
  type BranchSet,
} from "@/lib/branches";

interface Msg {
  id: string;
  role: "user" | "assistant";
  content: string;
  at?: string;
  steer?: boolean;
  branch?: BranchSet<Msg>;
}

const u = (id: string, extra: Partial<Msg> = {}): Msg => ({ id, role: "user", content: `q ${id}`, ...extra });
const a = (id: string, extra: Partial<Msg> = {}): Msg => ({ id, role: "assistant", content: `a ${id}`, ...extra });
const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x)) as T;
const ids = (ms: Msg[]) => ms.map((m) => m.id);

function base(): Msg[] {
  return [u("u1"), a("a1"), u("u2"), a("a2")];
}

describe("forkTail — another answer (regenerate, index = last)", () => {
  it("stores the old reply and makes the new one live, numbered last", () => {
    const msgs = base();
    const before = clone(msgs);
    const { messages, dropped } = forkTail(msgs, 3, [a("a2b")]);
    expect(dropped).toBe(false);
    expect(ids(messages)).toEqual(["u1", "a1", "u2", "a2b"]);
    expect(messages[3].branch).toEqual({ tails: [[a("a2")], null] });
    expect(branchInfo(messages[3])).toEqual({ pos: 1, count: 2 });
    // pure: the input is untouched
    expect(msgs).toEqual(before);
    // the prefix is shared, not rebuilt
    expect(messages[0]).toBe(msgs[0]);
  });

  it("a second 'try again' extends the same fork: 3 / 3, oldest first", () => {
    const one = forkTail(base(), 3, [a("a2b")]).messages;
    const two = forkTail(one, 3, [a("a2c")]).messages;
    expect(branchInfo(two[3])).toEqual({ pos: 2, count: 3 });
    const tails = two[3].branch!.tails;
    expect(tails[0]).toEqual([a("a2")]);
    expect(tails[1]).toEqual([a("a2b")]); // stored WITHOUT its branch field
    expect("branch" in (tails[1] as Msg[])[0]).toBe(false);
    expect(tails[2]).toBeNull();
  });
});

describe("forkTail — an edited question (index = the user message)", () => {
  it("stores the whole old tail (question + reply) and the edit is live", () => {
    const { messages } = forkTail(base(), 2, [u("u2b")]);
    expect(ids(messages)).toEqual(["u1", "a1", "u2b"]);
    expect(messages[2].branch).toEqual({ tails: [[u("u2"), a("a2")], null] });
    // the reply the turn adds later lands after it; the fork rides along
    const answered = [...messages, a("a2x")];
    expect(branchInfo(answered[2])).toEqual({ pos: 1, count: 2 });
  });

  it("switching back shows the original question and its answer", () => {
    const edited = [...forkTail(base(), 2, [u("u2b")]).messages, a("a2x")];
    const back = switchBranch(edited, 2, 0);
    expect(ids(back)).toEqual(["u1", "a1", "u2", "a2"]);
    expect(branchInfo(back[2])).toEqual({ pos: 0, count: 2 });
    expect(back[2].branch!.tails[1]).toEqual([u("u2b"), a("a2x")]);
    expect("branch" in (back[2].branch!.tails[1] as Msg[])[0]).toBe(false);
  });
});

describe("switchBranch — round trips", () => {
  it("away and back is the identical conversation", () => {
    const edited = [...forkTail(base(), 2, [u("u2b")]).messages, a("a2x")];
    const snapshot = clone(edited);
    expect(switchBranch(switchBranch(edited, 2, 0), 2, 1)).toEqual(snapshot);
  });

  it("cycling through three versions comes home unchanged", () => {
    let m = forkTail(base(), 3, [a("b")]).messages;
    m = forkTail(m, 3, [a("c")]).messages;
    const home = clone(m);
    const seen: string[] = [];
    for (const to of [0, 1, 2, 0, 2]) {
      m = switchBranch(m, 3, to);
      seen.push(m[3].id);
      expect(branchInfo(m[3])).toEqual({ pos: to, count: 3 });
    }
    expect(seen).toEqual(["a2", "b", "c", "a2", "c"]);
    expect(m).toEqual(home);
  });

  it("survives a save: the JSON round trip behaves the same", () => {
    const edited = [...forkTail(base(), 2, [u("u2b")]).messages, a("a2x")];
    const saved = clone(edited);
    expect(switchBranch(saved, 2, 0)).toEqual(switchBranch(edited, 2, 0));
  });

  it("bad input is a no-op that returns the SAME array", () => {
    const m = forkTail(base(), 3, [a("b")]).messages;
    expect(switchBranch(m, 3, 1)).toBe(m); // already live
    expect(switchBranch(m, 3, 2)).toBe(m); // out of range
    expect(switchBranch(m, 3, -1)).toBe(m);
    expect(switchBranch(m, 3, 0.5)).toBe(m);
    expect(switchBranch(m, 2, 0)).toBe(m); // no fork there
    expect(switchBranch(m, 9, 0)).toBe(m);
    expect(switchBranch(m, -1, 0)).toBe(m);
  });

  it("a malformed stored fork is treated as none", () => {
    const twoLive = [u("x", { branch: { tails: [null, null] } })];
    const emptyTail = [u("x", { branch: { tails: [[], null] } })];
    const noLive = [u("x", { branch: { tails: [[u("y")], [u("z")]] } })];
    const notArray = [u("x", { branch: { tails: "nope" } as unknown as BranchSet<Msg> })];
    for (const m of [twoLive, emptyTail, noLive, notArray]) {
      expect(branchInfo(m[0])).toBeNull();
      expect(switchBranch(m, 0, 0)).toBe(m);
    }
  });
});

describe("nested forks are kept inside stored tails", () => {
  it("an answer-fork under an edited question comes back with the question", () => {
    // try again on the last reply → the reply carries a fork
    const regen = forkTail(base(), 3, [a("a2b")]).messages;
    // then edit the question above it → the whole tail (with the nested fork) is stored
    const edited = [...forkTail(regen, 2, [u("u2b")]).messages, a("z")];
    const stored = edited[2].branch!.tails[0] as Msg[];
    expect(ids(stored)).toEqual(["u2", "a2b"]);
    expect(branchInfo(stored[1])).toEqual({ pos: 1, count: 2 });
    // switch back: the nested picker is still there and still works
    const back = switchBranch(edited, 2, 0);
    expect(ids(back)).toEqual(["u1", "a1", "u2", "a2b"]);
    expect(branchInfo(back[3])).toEqual({ pos: 1, count: 2 });
    const inner = switchBranch(back, 3, 0);
    expect(ids(inner)).toEqual(["u1", "a1", "u2", "a2"]);
    // and both levels round-trip
    expect(switchBranch(switchBranch(inner, 3, 1), 2, 1)).toEqual(edited);
  });

  it("forking at an earlier message keeps later forks' sets intact", () => {
    const deep = forkTail(base(), 3, [a("a2b")]).messages;
    const early = forkTail(deep, 0, [u("u1b")]).messages;
    const stored = early[0].branch!.tails[0] as Msg[];
    expect(stored).toHaveLength(4);
    expect(stored[3].branch).toEqual(deep[3].branch);
  });
});

describe("the cap", () => {
  it(`keeps at most ${MAX_BRANCHES} versions and drops the OLDEST stored one`, () => {
    let m = base();
    const drops: boolean[] = [];
    for (let k = 1; k <= 12; k++) {
      const r = forkTail(m, 3, [a(`v${k}`)]);
      m = r.messages;
      drops.push(r.dropped);
      expect(branchInfo(m[3])).toEqual({ pos: Math.min(k + 1, MAX_BRANCHES) - 1, count: Math.min(k + 1, MAX_BRANCHES) });
    }
    // fork 9 makes 10 versions (no drop); forks 10..12 each drop one
    expect(drops).toEqual([false, false, false, false, false, false, false, false, false, true, true, true]);
    const tails = m[3].branch!.tails;
    // the original a2, v1 and v2 are gone; v3 is now the oldest kept
    expect((tails[0] as Msg[])[0].id).toBe("v3");
    expect((tails[MAX_BRANCHES - 2] as Msg[])[0].id).toBe("v11");
    expect(tails[MAX_BRANCHES - 1]).toBeNull();
    expect(m[3].id).toBe("v12");
  });

  it("drops the oldest STORED version even when the live one sits first", () => {
    let m = base();
    for (let k = 1; k < MAX_BRANCHES; k++) m = forkTail(m, 3, [a(`v${k}`)]).messages;
    m = switchBranch(m, 3, 0); // the original is live, in slot 0
    expect(m[3].id).toBe("a2");
    const r = forkTail(m, 3, [a("new")]);
    expect(r.dropped).toBe(true);
    const tails = r.messages[3].branch!.tails;
    expect(tails).toHaveLength(MAX_BRANCHES);
    // the original (live → now stored in slot 0) is KEPT; v1 (the oldest stored) went
    expect((tails[0] as Msg[])[0].id).toBe("a2");
    expect(tails.some((t) => t && t[0].id === "v1")).toBe(false);
  });
});

describe("edges", () => {
  it("index === length is a plain append with no fork", () => {
    const { messages, dropped } = forkTail(base(), 4, [u("u3"), a("a3")]);
    expect(ids(messages)).toEqual(["u1", "a1", "u2", "a2", "u3", "a3"]);
    expect(messages.some((m) => m.branch)).toBe(false);
    expect(dropped).toBe(false);
  });

  it("an empty new tail or a bad index changes nothing", () => {
    const m = base();
    expect(forkTail(m, 3, []).messages).toBe(m);
    expect(forkTail(m, -1, [a("x")]).messages).toBe(m);
    expect(forkTail(m, 5, [a("x")]).messages).toBe(m);
    expect(forkTail(m, 1.5, [a("x")]).messages).toBe(m);
  });

  it("the new tail's own branch field is replaced by the fork's", () => {
    const stray = a("n", { branch: { tails: [[a("junk")], null] } });
    const { messages } = forkTail(base(), 3, [stray]);
    expect(messages[3].branch).toEqual({ tails: [[a("a2")], null] });
  });

  it("a multi-message new tail (steer notes, then the reply) forks on its first", () => {
    const { messages } = forkTail(base(), 3, [u("s", { steer: true }), a("r")]);
    expect(ids(messages)).toEqual(["u1", "a1", "u2", "s", "r"]);
    expect(branchInfo(messages[3])).toEqual({ pos: 1, count: 2 });
    expect(messages[4].branch).toBeUndefined();
  });

  it("branchInfo / stripBranches", () => {
    expect(branchInfo(undefined)).toBeNull();
    expect(branchInfo(u("x"))).toBeNull();
    const plain = u("x");
    expect(stripBranches(plain)).toBe(plain);
    const forked = forkTail(base(), 3, [a("b")]).messages[3];
    const bare = stripBranches(forked);
    expect("branch" in bare).toBe(false);
    expect(bare).toEqual(a("b"));
    expect(forked.branch).toBeDefined(); // not mutated
  });
});

describe("forkOnto — the fork for a turn that already produced its messages", () => {
  it("regenerate: prefix from AFTER (fresh fields), stored tail from BEFORE", () => {
    const before = base();
    const after = [u("u1"), a("a1"), u("u2", { at: "2026-10-09T10:00:00Z" }), a("a2new")];
    const { messages } = forkOnto(before, 3, after);
    expect(messages[2].at).toBe("2026-10-09T10:00:00Z");
    expect(ids(messages)).toEqual(["u1", "a1", "u2", "a2new"]);
    expect(messages[3].branch).toEqual({ tails: [[a("a2")], null] });
  });

  it("edit: forkOnto(before, i, [...kept, edited]) before the turn runs", () => {
    const before = base();
    const kept = before.slice(0, 2);
    const { messages } = forkOnto(before, 2, [...kept, u("u2b")]);
    expect(ids(messages)).toEqual(["u1", "a1", "u2b"]);
    expect(messages[2].branch).toEqual({ tails: [[u("u2"), a("a2")], null] });
  });

  it("while the new tail does not exist yet, AFTER comes back unchanged", () => {
    const before = base();
    const history = before.slice(0, 3);
    expect(forkOnto(before, 3, history).messages).toBe(history);
    expect(forkOnto(before, 9, before).messages).toBe(before);
  });
});

/**
 * v1.327.0 (calm chat W2-3) — the reply's details name the project rules and
 * the earlier chats the turn read.
 *
 * Since v1.326.0 the POST /chat response and the /chat/stream done frame
 * ALWAYS carry `folder_rules` (["AGENTS.md", "CLAUDE.md"]) and `thread_refs`
 * ([{id, title, chars, ok, note}]). This file pins:
 *  - the whitelist decoders and the plain words (lib/turnReads);
 *  - which left-out reference WARNS, and that the quiet notes are the
 *    daemon's own sentences (read from daemon/chat_refs.py);
 *  - the expanded TurnReceipt lines, the small amber count on the collapsed
 *    line, nothing at all for an older message, receiptWantsAttention;
 *  - the done frame reaching the hook's resolved result.
 * The page's two lanes are pinned in chat-turn-reads-v1327.test.tsx.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { TurnReceipt, receiptWantsAttention } from "@/components/chat/TurnReceipt";
import {
  QUIET_REF_NOTES,
  decodeFolderRules,
  decodeThreadRefs,
  folderRulesLine,
  refWarningCount,
  refWarns,
  threadRefLines,
} from "@/lib/turnReads";
import { decodeSSE, useChatStream } from "@/lib/useChatStream";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ROUTE = { requested: "", provider: "claude-cli", model: "claude-opus", reason: "default" };
const NOT_FOUND = "No saved chat was found for this reference.";
const CURRENT = "This is the chat you are in, so it was not added again.";
const SHORTENED = "Only its most recent messages were added.";

function read(rel: string): string {
  return readFileSync(resolve(__dirname, rel), "utf8").replace(/\r\n/g, "\n");
}

describe("lib/turnReads — decoders and words", () => {
  it("folder rules: strings only, one line, deduped, capped", () => {
    expect(decodeFolderRules(["AGENTS.md", "", "  ", 7, null, "CLAUDE.md", "AGENTS.md"])).toEqual([
      "AGENTS.md",
      "CLAUDE.md",
    ]);
    expect(decodeFolderRules("AGENTS.md")).toEqual([]);
    expect(decodeFolderRules(undefined)).toEqual([]);
    expect(decodeFolderRules(Array.from({ length: 20 }, (_, i) => `R${i}.md`))).toHaveLength(8);
  });

  it("thread refs: rows need an id; ok only when true; chars a whole number", () => {
    expect(
      decodeThreadRefs([
        { id: "t1", title: "Tax plan\n2026", chars: 812.7, ok: true, note: "", extra: "x" },
        { id: "t2", title: "", chars: "9", ok: "yes", note: NOT_FOUND },
        { title: "no id", ok: true },
        "junk",
        null,
      ]),
    ).toEqual([
      { id: "t1", title: "Tax plan 2026", chars: 812, ok: true, note: "" },
      { id: "t2", title: "", chars: 0, ok: false, note: NOT_FOUND },
    ]);
    expect(decodeThreadRefs({ id: "t1" })).toEqual([]);
  });

  it("says the folder rules in plain words", () => {
    expect(folderRulesLine(["AGENTS.md"])).toBe("Followed the project's AGENTS.md");
    expect(folderRulesLine(["AGENTS.md", "CLAUDE.md"])).toBe(
      "Followed the project's AGENTS.md and CLAUDE.md",
    );
    expect(folderRulesLine(["AGENTS.md", "CLAUDE.md", "CLAUDE.local.md"])).toBe(
      "Followed the project's AGENTS.md, CLAUDE.md and CLAUDE.local.md",
    );
    expect(folderRulesLine([])).toBeNull();
    expect(folderRulesLine(undefined)).toBeNull();
  });

  it("says the chats read, then a line per note; only a left-out warning warns", () => {
    const lines = threadRefLines([
      { id: "a", title: "Tax plan", chars: 900, ok: true, note: "" },
      { id: "b", title: "Q3 notes", chars: 400, ok: true, note: SHORTENED },
      { id: "c", title: "", chars: 0, ok: false, note: NOT_FOUND },
      { id: "d", title: "This chat", chars: 0, ok: false, note: CURRENT },
    ]);
    expect(lines.read).toBe("Read 2 earlier chats: Tax plan, Q3 notes");
    expect(lines.notes.map((n) => [n.text, n.warn])).toEqual([
      [`Q3 notes: ${SHORTENED}`, false],
      [NOT_FOUND, true],
      [`This chat: ${CURRENT}`, false],
    ]);
    expect(threadRefLines([{ id: "a", title: "", chars: 5, ok: true, note: "" }]).read).toBe(
      "Read 1 earlier chat: an untitled chat",
    );
    // A left-out row with no note still says so, and warns.
    const bare = threadRefLines([{ id: "z", title: "Old", chars: 0, ok: false, note: "" }]);
    expect(bare.read).toBeNull();
    expect(bare.notes).toEqual([{ key: "z-0", text: "Old: That chat could not be read.", warn: true }]);
  });

  it("a reason this client has never heard of warns (honest default)", () => {
    expect(refWarns({ id: "x", title: "", chars: 0, ok: false, note: "Something new happened." })).toBe(true);
    expect(refWarns({ id: "x", title: "", chars: 0, ok: true, note: "Something new happened." })).toBe(false);
    expect(refWarningCount([{ id: "x", ok: false, note: NOT_FOUND }, { id: "y", ok: false, note: CURRENT }])).toBe(1);
  });

  it("the quiet notes are the daemon's own sentences, word for word", () => {
    const py = read("../../src/iron_jarvis/daemon/chat_refs.py");
    const current = /^NOTE_CURRENT = "([^"]+)"/m.exec(py)?.[1];
    const empty = /^NOTE_EMPTY = "([^"]+)"/m.exec(py)?.[1];
    expect(current).toBeTruthy();
    expect(empty).toBeTruthy();
    expect([...QUIET_REF_NOTES].sort()).toEqual([current!, empty!].sort());
    // And the warning ones are NOT quiet.
    const notFound = /^NOTE_NOT_FOUND = "([^"]+)"/m.exec(py)?.[1];
    const unreadable = /^NOTE_UNREADABLE = "([^"]+)"/m.exec(py)?.[1];
    expect(QUIET_REF_NOTES).not.toContain(notFound);
    expect(QUIET_REF_NOTES).not.toContain(unreadable);
  });
});

describe("TurnReceipt — what the turn read (v1.327.0)", () => {
  function openInline() {
    fireEvent.click(screen.getByTestId("turn-receipt"));
    return screen.getByTestId("turn-receipt-detail");
  }

  it("names the project rules and the chats read in the expanded detail", () => {
    render(
      <TurnReceipt
        inline
        route={ROUTE}
        folderRules={["AGENTS.md", "CLAUDE.md"]}
        threadRefs={[
          { id: "a", title: "Tax plan", chars: 900, ok: true, note: "" },
          { id: "b", title: "Q3 notes", chars: 400, ok: true, note: "" },
        ]}
      />,
    );
    // Collapsed: nothing extra on the line (no warning, no count).
    const toggle = screen.getByTestId("turn-receipt");
    expect(toggle.textContent).not.toMatch(/AGENTS|chat|Followed/);
    expect(screen.queryByTestId("turn-refs-left-out")).toBeNull();
    expect(screen.queryByTestId("turn-folder-rules")).toBeNull();
    openInline();
    expect(screen.getByTestId("turn-folder-rules").textContent).toBe(
      "Followed the project's AGENTS.md and CLAUDE.md",
    );
    expect(screen.getByTestId("turn-thread-refs").textContent).toBe(
      "Read 2 earlier chats: Tax plan, Q3 notes",
    );
    expect(screen.queryByTestId("turn-thread-ref-note")).toBeNull();
  });

  it("a left-out chat shows its note, amber only when it warns, and a small count on the line", () => {
    render(
      <TurnReceipt
        inline
        route={ROUTE}
        threadRefs={[
          { id: "a", title: "Tax plan", chars: 900, ok: true, note: "" },
          { id: "c", title: "", chars: 0, ok: false, note: NOT_FOUND },
          { id: "d", title: "This chat", chars: 0, ok: false, note: CURRENT },
        ]}
      />,
    );
    const count = screen.getByTestId("turn-refs-left-out");
    expect(count.textContent).toBe("1 chat left out");
    expect(count.className).toContain("text-tone-warn");
    openInline();
    const notes = screen.getAllByTestId("turn-thread-ref-note");
    expect(notes.map((n) => [n.textContent, n.dataset.warn])).toEqual([
      [NOT_FOUND, "true"],
      [`This chat: ${CURRENT}`, "false"],
    ]);
    expect(notes[0].className).toContain("text-tone-warn");
    expect(notes[1].className).not.toContain("tone-warn");
    expect(notes[1].className).toContain("text-zinc-500");
  });

  it("an older message without the fields says nothing about them", () => {
    render(<TurnReceipt inline route={ROUTE} />);
    openInline();
    expect(screen.queryByTestId("turn-folder-rules")).toBeNull();
    expect(screen.queryByTestId("turn-thread-refs")).toBeNull();
    expect(screen.queryByTestId("turn-thread-ref-note")).toBeNull();
    expect(screen.queryByTestId("turn-refs-left-out")).toBeNull();
  });

  it("junk read back from a saved chat says nothing", () => {
    render(
      <TurnReceipt
        inline
        route={ROUTE}
        folderRules={"AGENTS.md" as unknown as string[]}
        threadRefs={[{ nope: 1 }] as unknown as never[]}
      />,
    );
    openInline();
    expect(screen.queryByTestId("turn-folder-rules")).toBeNull();
    expect(screen.queryByTestId("turn-thread-refs")).toBeNull();
  });

  it("the stand-alone receipt renders the same lines (other surfaces)", () => {
    render(<TurnReceipt folderRules={["AGENTS.md"]} />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.getByTestId("turn-folder-rules").textContent).toBe("Followed the project's AGENTS.md");
  });

  it("a left-out chat keeps the reply's row on screen; a read one does not", () => {
    expect(
      receiptWantsAttention({ route: ROUTE, threadRefs: [{ id: "c", title: "", chars: 0, ok: false, note: NOT_FOUND }] }),
    ).toBe(true);
    expect(
      receiptWantsAttention({ route: ROUTE, threadRefs: [{ id: "a", title: "T", chars: 9, ok: true, note: "" }] }),
    ).toBe(false);
    expect(
      receiptWantsAttention({ route: ROUTE, threadRefs: [{ id: "d", title: "", chars: 0, ok: false, note: CURRENT }] }),
    ).toBe(false);
    expect(receiptWantsAttention({ route: ROUTE })).toBe(false);
  });
});

describe("the done frame's folder_rules and thread_refs reach the result", () => {
  const RAW = {
    reply: "ok",
    folder_rules: ["AGENTS.md", "CLAUDE.md", 3],
    thread_refs: [
      { id: "a", title: "Tax plan", chars: 900, ok: true, note: "", extra: 1 },
      { id: "c", title: "", chars: 0, ok: false, note: NOT_FOUND },
    ],
  };

  it("decodes with a whitelist; empty lists leave the keys absent", () => {
    const ev = decodeSSE("done", JSON.stringify(RAW)) as { folder_rules?: string[]; thread_refs?: unknown[] };
    expect(ev.folder_rules).toEqual(["AGENTS.md", "CLAUDE.md"]);
    expect(ev.thread_refs).toEqual([
      { id: "a", title: "Tax plan", chars: 900, ok: true, note: "" },
      { id: "c", title: "", chars: 0, ok: false, note: NOT_FOUND },
    ]);
    const none = decodeSSE("done", JSON.stringify({ reply: "r", folder_rules: [], thread_refs: [] })) as object;
    expect("folder_rules" in none).toBe(false);
    expect("thread_refs" in none).toBe(false);
  });

  it("the real hook carries both onto the resolved result", async () => {
    const frames = `event: done\ndata: ${JSON.stringify(RAW)}\n\n`;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(frames));
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(stream, { status: 200 })));
    const { result } = renderHook(() => useChatStream());
    let settled: { folderRules?: string[]; threadRefs?: unknown[] } | null = null;
    await act(async () => {
      settled = (await result.current.run({ messages: [] })) as typeof settled;
    });
    expect(settled!.folderRules).toEqual(["AGENTS.md", "CLAUDE.md"]);
    expect(settled!.threadRefs).toEqual([
      { id: "a", title: "Tax plan", chars: 900, ok: true, note: "" },
      { id: "c", title: "", chars: 0, ok: false, note: NOT_FOUND },
    ]);
  });
});

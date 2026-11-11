/**
 * v1.472.0: a Build › Code reply still being written parses only its tail.
 * The settled part (before the last blank line outside a code fence) is
 * parsed once; the whole reply used to be parsed again on every frame.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";

const parsed = vi.hoisted(() => ({ memo: [] as string[], plain: [] as string[] }));

vi.mock("@/components/Markdown", async (orig) => {
  const real = await orig<typeof import("@/components/Markdown")>();
  const R = await import("react");
  const Markdown = ({ content }: { content: string }) => {
    parsed.plain.push(content);
    return R.createElement("p", null, content);
  };
  const MemoMarkdown = R.memo(function MemoMarkdown({ content }: { content: string }) {
    parsed.memo.push(content);
    return R.createElement("p", null, content);
  });
  return { ...real, Markdown, MemoMarkdown };
});

import { CodeConversation } from "@/components/buildCode/CodeConversation";

afterEach(() => {
  cleanup();
  parsed.memo.length = 0;
  parsed.plain.length = 0;
});

describe("a streaming Build › Code reply", () => {
  it("parses the settled part once and only the tail on later frames", () => {
    const item = (text: string, done = false) => ({ kind: "text" as const, key: "t-1", text, done });
    const view = render(<CodeConversation items={[item("One.\n\nTwo")]} folder="" />);
    view.rerender(<CodeConversation items={[item("One.\n\nTwo more")]} folder="" />);
    view.rerender(<CodeConversation items={[item("One.\n\nTwo more words")]} folder="" />);
    expect(parsed.memo).toEqual(["One.\n\n"]);
    expect(parsed.plain).toEqual(["Two", "Two more", "Two more words"]);
    // Done: the whole reply, once.
    view.rerender(<CodeConversation items={[item("One.\n\nTwo more words.", true)]} folder="" />);
    expect(parsed.memo.at(-1)).toBe("One.\n\nTwo more words.");
  });
});

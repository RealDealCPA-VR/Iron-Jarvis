/**
 * v1.330.0 (calm chat wave 12, N2): the Draft card is never empty, its footer
 * speaks in plain sentences, and a no-dash guard reads it.
 *
 * A real glm reply opened with an empty draft fence, and the chat drew the
 * Draft card (Save to Drafts, Send, Copy) around nothing above the reply.
 * `draftFromFence` made a card for ANY email/draft/message fence. Now:
 *
 * - a blank fence (empty or only whitespace) draws NOTHING: no card and no
 *   empty code block (`blankDraftFence`, checked in components/Markdown.tsx);
 * - a fence holding only header lines (To:, Subject:, Cc: and the like) is
 *   not a card and shows as the plain code block, so what the model wrote is
 *   still readable;
 * - every draft with words to send is still a card.
 *
 * These tests render through the REAL renderer (components/Markdown), the
 * call site the chat uses, so dropping the check there turns them red.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("@/lib/api", () => ({ API_BASE: "http://127.0.0.1:8787", ijToken: () => "" }));

import { Markdown } from "@/components/Markdown";
import { blankDraftFence, DraftCard, draftHasBody } from "@/components/chat/DraftCard";
import { asides, copyPieces } from "./helpers/dashGuard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const fence = (lang: string, body: string) => "```" + lang + "\n" + body + "\n```";

describe("a draft fence with nothing to send is not a card", () => {
  it("an empty fence before the reply draws nothing at all (the glm reply)", () => {
    const { container } = render(
      <Markdown content={"```draft\n```\n\nI don't have a file tool in this chat, so here is what I can say."} />,
    );
    expect(screen.queryByTestId("draft-card")).toBeNull();
    expect(container.querySelector("pre")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
    // The prose after it is untouched.
    expect(container.textContent).toContain("I don't have a file tool in this chat");
  });

  it("a whitespace-only fence draws nothing, for every draft word", () => {
    for (const lang of ["email", "draft", "message"]) {
      const { container } = render(<Markdown content={fence(lang, "   \n\n\t")} />);
      expect(screen.queryByTestId("draft-card"), lang).toBeNull();
      expect(container.querySelector("pre"), lang).toBeNull();
      expect(container.textContent?.trim(), lang).toBe("");
      cleanup();
    }
  });

  it("a fence of headers only is shown as plain code, not as a card", () => {
    const { container } = render(<Markdown content={fence("email", "To: dana@example.com\nSubject: Q3 close")} />);
    expect(screen.queryByTestId("draft-card")).toBeNull();
    const pre = container.querySelector("pre");
    expect(pre).not.toBeNull();
    expect(pre!.textContent).toContain("To: dana@example.com");
    expect(pre!.textContent).toContain("Subject: Q3 close");
    expect(screen.queryByRole("button", { name: /save to drafts/i })).toBeNull();
  });

  it("a fence holding only an empty Subject: line is not a card either", () => {
    render(<Markdown content={fence("message", "Subject:")} />);
    expect(screen.queryByTestId("draft-card")).toBeNull();
  });
});

describe("every real draft is still a card", () => {
  it("subject and body", () => {
    render(<Markdown content={fence("email", "Subject: Q3 close\n\nHi Dana,\n\nWe are **all set**.")} />);
    expect(screen.getByTestId("draft-card")).toBeTruthy();
    expect(screen.getByText("Q3 close")).toBeTruthy();
    expect(screen.getByTestId("draft-body").querySelector("strong")?.textContent).toBe("all set");
  });

  it("To: headers and a body", () => {
    render(<Markdown content={fence("email", "To: dana@example.com\nCc: sam@example.com\n\nHi Dana,\n\nAll set.")} />);
    expect(screen.getByTestId("draft-card")).toBeTruthy();
    expect(screen.getByTestId("draft-body").textContent).toContain("All set.");
  });

  it("a one-line message with no subject", () => {
    render(<Markdown content={fence("message", "Running ten minutes late, see you soon.")} />);
    expect(screen.getByTestId("draft-card")).toBeTruthy();
    expect(screen.getByText("Draft")).toBeTruthy();
  });

  it("an ordinary code fence is untouched", () => {
    const { container } = render(<Markdown content={fence("python", "print('hi')")} />);
    expect(screen.queryByTestId("draft-card")).toBeNull();
    expect(container.querySelector("pre")?.textContent).toContain("print('hi')");
  });

  it("an empty ordinary code fence is not dropped (only draft fences are)", () => {
    const { container } = render(<Markdown content={fence("python", "")} />);
    expect(container.querySelector("pre")).not.toBeNull();
  });
});

describe("draftHasBody and blankDraftFence", () => {
  it("reads only the leading lines as headers", () => {
    const table: Array<[string, boolean]> = [
      ["", false],
      ["   \n \n", false],
      ["Subject: Hi", false],
      ["Subject:", false],
      ["To: a@b.com", false],
      ["To: a@b.com\nCc: c@d.com\nBcc: e@f.com\nFrom: me@x.com\nReply-To: me@x.com\n\n", false],
      ["Subject: Hi\n\nTo: a@b.com", false],
      ["Hi Dana", true],
      ["Subject: Hi\n\nThanks.", true],
      ["To: a@b.com\n\nThanks.", true],
      // A "To:" below the first words is part of the message.
      ["Dear team,\nTo: whom it may concern", true],
    ];
    for (const [raw, want] of table) expect(draftHasBody(raw), JSON.stringify(raw)).toBe(want);
  });

  it("blankDraftFence is true only for a draft word with no text", () => {
    const code = (lang: string, body: string) => <code className={`language-${lang}`}>{body}</code>;
    expect(blankDraftFence(code("email", ""), "")).toBe(true);
    expect(blankDraftFence(code("draft", "  \n"), "  \n")).toBe(true);
    expect(blankDraftFence(code("email", "To: a@b.com"), "To: a@b.com")).toBe(false);
    expect(blankDraftFence(code("python", ""), "")).toBe(false);
  });
});

describe("the footer speaks in plain sentences", () => {
  it("the idle note", () => {
    render(
      <DraftCard subject="S" text="Body">
        <p>Body</p>
      </DraftCard>,
    );
    expect(screen.getByTestId("draft-note").textContent).toBe("Paste into your email. The formatting is kept.");
  });

  it("the note after a copy that could only write plain text", async () => {
    vi.stubGlobal("ClipboardItem", undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText: vi.fn(async () => {}) } });
    render(
      <DraftCard text="Body">
        <p>Body</p>
      </DraftCard>,
    );
    fireEvent.click(screen.getByRole("button", { name: /^copy$/i }));
    await waitFor(() =>
      expect(screen.getByTestId("draft-note").textContent).toBe(
        "Formatting could not be copied here. It pastes as plain text.",
      ),
    );
  });

  it("no spaced em or en dash in any string or JSX text of the card", () => {
    expect(asides("components/chat/DraftCard.tsx")).toEqual([]);
  });

  it("the guard reads the card's footer words (anti-vacuity)", () => {
    const words = copyPieces("components/chat/DraftCard.tsx").map((p) => p.text);
    expect(words).toContain("Paste into your email. The formatting is kept.");
    expect(words).toContain("Formatting could not be copied here. It pastes as plain text.");
    // And the walker would see a dash aside in this file's own shape.
    expect(asides("probe.tsx", '<span>{x ? "a — b" : "c"}</span>')).toHaveLength(1);
  });
});

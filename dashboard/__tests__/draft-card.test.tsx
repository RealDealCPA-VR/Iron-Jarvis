/**
 * The copyable draft card (v1.161.0).
 *
 * WHAT ACTUALLY MATTERS HERE is the clipboard FLAVOUR. The box is cosmetic; the
 * reason the feature exists is that a draft copied as `text/plain` and pasted
 * into Gmail or Outlook arrives with its bold as literal asterisks and its
 * lists as hyphens, so the user reformats by hand — the work they asked the
 * assistant to do. So the tests that carry weight assert that `text/html` is
 * written, that it carries the subject and the semantic tags, and that the
 * app's dark-theme classes are NOT carried across.
 *
 * The other load-bearing one is `reports a degraded copy honestly`. A surface
 * that can only manage `writeText` is a real outcome (an older browser, a
 * refused permission), and telling the user "Copied" there is a lie about the
 * single thing this component does — they paste and the formatting is gone.
 */

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Children, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  DRAFT_LANGS,
  DraftCard,
  cleanHtml,
  copyRich,
  fenceLang,
  splitSubject,
} from "@/components/chat/DraftCard";

type Written = { html?: string; text?: string };

function stubRichClipboard(): Written {
  const seen: Written = {};
  class FakeClipboardItem {
    constructor(public items: Record<string, Blob>) {}
  }
  vi.stubGlobal("ClipboardItem", FakeClipboardItem);
  vi.stubGlobal("navigator", {
    clipboard: {
      write: vi.fn(async (items: FakeClipboardItem[]) => {
        for (const [type, blob] of Object.entries(items[0].items)) {
          const body = await blob.text();
          if (type === "text/html") seen.html = body;
          if (type === "text/plain") seen.text = body;
        }
      }),
      writeText: vi.fn(async (t: string) => {
        seen.text = t;
      }),
    },
  });
  return seen;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
});

describe("splitSubject", () => {
  it("lifts a leading Subject line out of the body", () => {
    const { subject, body } = splitSubject("Subject: Q3 close\n\nHi Dana,\n\nAll set.");
    expect(subject).toBe("Q3 close");
    expect(body).toBe("Hi Dana,\n\nAll set.");
  });

  it("leaves a mid-body Subject alone", () => {
    // Promoting this would DELETE a line from the message — the user is quoting
    // a thread, not naming their own subject.
    const raw = "Hi Dana,\n\nForwarding the below.\n\nSubject: Q3 close\n\nThanks";
    const { subject, body } = splitSubject(raw);
    expect(subject).toBeUndefined();
    expect(body).toContain("Subject: Q3 close");
  });

  it("is case-insensitive and survives a blank first line", () => {
    expect(splitSubject("\nSUBJECT:  Payment due \nBody").subject).toBe("Payment due");
  });

  it("ignores an empty subject rather than showing a blank header", () => {
    expect(splitSubject("Subject:   \n\nBody here").subject).toBeUndefined();
  });
});

describe("fenceLang", () => {
  // THE WIRING. Every other test in this file passes even when nothing renders,
  // because they construct the card directly. This is the seam that decides
  // whether a ```email fence in a real reply ever becomes a card at all.
  it("reads the language react-markdown actually puts on the code element", () => {
    expect(fenceLang(<code className="language-email">body</code>)).toBe("email");
  });

  it("is empty for a plain fence, so ordinary code still renders as code", () => {
    expect(fenceLang(<code>plain</code>)).toBe("");
    expect(DRAFT_LANGS.has(fenceLang(<code className="language-python">x</code>))).toBe(
      false,
    );
  });

  it("accepts every word the server-side instruction may produce", () => {
    // Cross-checked against daemon/chat_turn.py DRAFT_BLOCK by
    // tests/test_draft_card_v1161.py — a word only one side knows is a
    // silent failure.
    for (const lang of ["email", "draft", "message"]) {
      expect(DRAFT_LANGS.has(lang)).toBe(true);
    }
  });
});

describe("the real markdown chain", () => {
  /**
   * Everything above assumes react-markdown hands `<pre>` a `<code>` carrying
   * `language-email`. That assumption is the whole feature, it belongs to a
   * dependency, and nothing else here would notice if a remark upgrade changed
   * it — the fence would quietly render as a grey code block. So this drives
   * the ACTUAL parser, wired the way chat/page.tsx wires it.
   */
  function Pre({ children }: { children?: ReactNode }) {
    if (!DRAFT_LANGS.has(fenceLang(children))) return <pre>{children}</pre>;
    const raw = String(
      (Children.toArray(children)[0] as { props?: { children?: unknown } })?.props
        ?.children ?? "",
    ).replace(/\n$/, "");
    const { subject, body } = splitSubject(raw);
    return (
      <DraftCard subject={subject} text={body}>
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
      </DraftCard>
    );
  }

  it("turns a ```email fence into a card, subject lifted and body formatted", () => {
    render(
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ pre: Pre }}>
        {"```email\nSubject: Q3 close\n\nHi Dana,\n\nWe are **all set**.\n```"}
      </ReactMarkdown>,
    );
    expect(screen.getByTestId("draft-card")).toBeTruthy();
    expect(screen.getByText("Q3 close")).toBeTruthy();
    // The body is real markdown, not literal asterisks — which is what makes
    // the copied text/html carry <strong> instead of "**all set**".
    expect(screen.getByTestId("draft-body").querySelector("strong")?.textContent).toBe(
      "all set",
    );
  });

  it("leaves an ordinary code fence as code", () => {
    render(
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ pre: Pre }}>
        {"```python\nprint('hi')\n```"}
      </ReactMarkdown>,
    );
    expect(screen.queryByTestId("draft-card")).toBeNull();
  });
});

describe("cleanHtml", () => {
  it("keeps the semantic tags and drops the app's presentation", () => {
    const node = document.createElement("div");
    node.innerHTML =
      '<p class="my-1.5 text-zinc-100" style="color:#fff">Hi <strong>Dana</strong></p>' +
      '<ul class="list-disc"><li>One</li></ul>' +
      '<a href="https://example.com" class="text-accent-soft">link</a>';
    const html = cleanHtml(node);
    expect(html).toContain("<strong>Dana</strong>");
    expect(html).toContain("<li>One</li>");
    expect(html).toContain('href="https://example.com"');
    // Dark-theme classes would either mean nothing in a composer or paste
    // white-on-white text.
    expect(html).not.toContain("text-zinc-100");
    expect(html).not.toContain("color:#fff");
  });

  it("does not strip the visible card while copying it", () => {
    const node = document.createElement("div");
    node.innerHTML = '<p class="keep-me">Hi</p>';
    cleanHtml(node);
    expect(node.innerHTML).toContain("keep-me");
  });
});

describe("DraftCard", () => {
  beforeEach(() => {
    vi.useRealTimers();
  });

  it("writes BOTH flavours, with the subject in each", async () => {
    const seen = stubRichClipboard();
    render(
      <DraftCard subject="Q3 close" text={"Hi Dana,\n\nAll set."}>
        <p>
          Hi <strong>Dana</strong>,
        </p>
      </DraftCard>,
    );
    fireEvent.click(screen.getByRole("button", { name: /^copy$/i }));
    await waitFor(() => expect(seen.html).toBeTruthy());
    expect(seen.html).toContain("Subject:");
    expect(seen.html).toContain("Q3 close");
    expect(seen.html).toContain("<strong>Dana</strong>");
    // The plain flavour is not optional: pasting into a plain-text field (or a
    // composer that refuses HTML) falls back to it, and an empty one is an
    // empty paste.
    expect(seen.text).toContain("Subject: Q3 close");
    expect(seen.text).toContain("Hi Dana,");
  });

  it("reports a degraded copy honestly", async () => {
    // Only writeText available — the older-browser / refused-permission case.
    vi.stubGlobal("ClipboardItem", undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText: vi.fn(async () => {}) } });
    render(
      <DraftCard subject="Q3 close" text="Body">
        <p>Body</p>
      </DraftCard>,
    );
    fireEvent.click(screen.getByRole("button", { name: /^copy$/i }));
    // "Copied" here would be a lie: the user pastes and the formatting is gone.
    expect(await screen.findByText(/copied as text/i)).toBeTruthy();
  });

  it("prefers the Electron bridge, which is never permission-gated", async () => {
    const writeHtml = vi.fn(async () => true);
    (window as unknown as { ironjarvis: unknown }).ironjarvis = {
      clipboardWriteHtml: writeHtml,
    };
    const seen = stubRichClipboard();
    render(
      <DraftCard subject="S" text="B">
        <p>B</p>
      </DraftCard>,
    );
    fireEvent.click(screen.getByRole("button", { name: /^copy$/i }));
    await waitFor(() => expect(writeHtml).toHaveBeenCalledTimes(1));
    expect(seen.html).toBeUndefined(); // the browser path was not used
  });

  it("falls back to the browser when the bridge throws", async () => {
    (window as unknown as { ironjarvis: unknown }).ironjarvis = {
      clipboardWriteHtml: vi.fn(async () => {
        throw new Error("no clipboard");
      }),
    };
    const seen = stubRichClipboard();
    render(
      <DraftCard subject="S" text="B">
        <p>B</p>
      </DraftCard>,
    );
    fireEvent.click(screen.getByRole("button", { name: /^copy$/i }));
    await waitFor(() => expect(seen.html).toBeTruthy());
  });

  it("copies the subject on its own, because it goes in its own field", async () => {
    const seen = stubRichClipboard();
    render(
      <DraftCard subject="Q3 close" text="Body">
        <p>Body</p>
      </DraftCard>,
    );
    fireEvent.click(screen.getByRole("button", { name: /copy subject/i }));
    await waitFor(() => expect(seen.text).toBe("Q3 close"));
  });

  it("renders without a subject rather than showing an empty header", () => {
    render(
      <DraftCard text="just a message">
        <p>just a message</p>
      </DraftCard>,
    );
    expect(screen.getByText("Draft")).toBeTruthy();
  });

  it("says the copy failed instead of showing a check", async () => {
    vi.stubGlobal("ClipboardItem", undefined);
    vi.stubGlobal("navigator", {
      clipboard: {
        writeText: vi.fn(async () => {
          throw new Error("denied");
        }),
      },
    });
    render(
      <DraftCard text="B">
        <p>B</p>
      </DraftCard>,
    );
    fireEvent.click(screen.getByRole("button", { name: /^copy$/i }));
    expect(await screen.findByText(/copy failed/i)).toBeTruthy();
  });
});

describe("copyRich", () => {
  it("returns 'plain' when only writeText exists", async () => {
    vi.stubGlobal("ClipboardItem", undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText: vi.fn(async () => {}) } });
    await expect(copyRich("<p>x</p>", "x")).resolves.toBe("plain");
  });

  it("returns 'rich' when both flavours land", async () => {
    stubRichClipboard();
    await expect(copyRich("<p>x</p>", "x")).resolves.toBe("rich");
  });
});

import { describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach } from "vitest";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  cleanHtml,
  draftFromFence,
  hardenLineBreaks,
  plainFromMarkdown,
} from "@/components/chat/DraftCard";

/**
 * THE DRAFT PASTES CLEAN (v1.215.1).
 *
 * Reported: "when i ask the model to provide an e-mail that i copy and paste,
 * it doesn't cleanly paste and i need to vigourously modify to get to the state
 * provided."
 *
 * The mechanism was already right and is worth restating, because it is the
 * thing that makes a clean paste possible at all: what you see is the card;
 * what the Copy button writes is a SEPARATE payload — a purpose-built
 * `text/html` plus a `text/plain` fallback. A clean paste comes from that
 * payload, not from the card being HTML on screen. (Which is why highlighting
 * an ordinary chat reply and copying it does not paste as well: no payload is
 * being built.)
 *
 * So the fault was IN the payload, and both halves were found by capturing the
 * real clipboard bytes from the shipping build rather than by reading the code:
 *
 *   text/html    Models hard-wrap at ~80 columns, and v1.163.0 hardened EVERY
 *                soft newline — so the wrap became a <br> and the pasted email
 *                carried a line break through the middle of a sentence:
 *                  "…I need three<br>things from you:"
 *   text/plain   was the fence body VERBATIM, i.e. markdown. Anything taking
 *                that flavour got "**October 1**" and "[the portal](https://…)"
 *                literally — and that flavour is what a plain-text composer
 *                takes, what Ctrl+Shift+V takes, and what the whole copy
 *                degrades to when the rich write is unavailable.
 */

const MD: Components = {
  p: ({ children }) => <p className="my-1.5">{children}</p>,
  ul: ({ children }) => <ul className="my-1.5 pl-5">{children}</ul>,
  li: ({ children }) => <li>{children}</li>,
};

/** The clipboard's `text/html`, built the way `DraftCard.copyAll` builds it. */
function htmlPayload(markdown: string): string {
  const { container } = render(
    <div>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD}>
        {hardenLineBreaks(markdown)}
      </ReactMarkdown>
    </div>,
  );
  return cleanHtml(container.firstElementChild as HTMLElement);
}

afterEach(cleanup);

/* --------------------------------------------------- the wrapped sentence --- */

describe("a wrapped source line is not a line break", () => {
  const WRAPPED =
    "To finish your return before the October 15 extended deadline, I need three\n" +
    "things from you:";

  it("leaves the newline soft, so the sentence pastes as one sentence", () => {
    // MEASURED against the shipping build: this produced
    // "I need three<br>\nthings from you:".
    expect(htmlPayload(WRAPPED)).not.toContain("<br>");
    expect(hardenLineBreaks(WRAPPED)).toBe(WRAPPED);
  });

  it("still hardens a signature, which is the case v1.163.0 existed for", () => {
    const html = htmlPayload("Best,\nValentino\nValentino Rossi, CPA");
    expect((html.match(/<br>/g) ?? []).length).toBe(2);
  });

  it("still hardens an address — short lines ended on purpose", () => {
    const html = htmlPayload("Acme LLC\n120 Main St\nSuite 4\nAustin, TX 78701");
    expect((html.match(/<br>/g) ?? []).length).toBe(3);
  });

  it("decides per BLOCK, so one paragraph's wrap cannot un-harden a signature", () => {
    const both =
      "Please send the documents listed above before the end of the month so we\n" +
      "have time to review them.\n\nBest,\nValentino";
    const html = htmlPayload(both);
    // Exactly the two breaks of the signature — none from the wrapped prose.
    expect((html.match(/<br>/g) ?? []).length).toBe(1);
    expect(html).toContain("Best,<br>");
  });

  it("skips blocks markdown already lays out itself", () => {
    // A hard break inside a list item is at best redundant; the list markers
    // are what put each item on its own line.
    const list = "- one\n- two\n- three";
    expect(hardenLineBreaks(list)).toBe(list);
    const quote = "> quoted one\n> quoted two";
    expect(hardenLineBreaks(quote)).toBe(quote);
  });

  it("still leaves fenced content alone", () => {
    const src = "text\n\n```\na\nb\n```";
    expect(hardenLineBreaks(src)).toBe(src);
  });

  it("is still idempotent", () => {
    const once = hardenLineBreaks("Best,\nValentino");
    expect(hardenLineBreaks(once)).toBe(once);
  });
});

/* ------------------------------------------------- the plain-text flavour --- */

describe("the plain flavour is plain TEXT, not markdown source", () => {
  it("unwraps emphasis but keeps the words", () => {
    expect(plainFromMarkdown("Due **October 1**, no *later*.")).toBe(
      "Due October 1, no later.",
    );
    expect(plainFromMarkdown("__bold__ and _italic_")).toBe("bold and italic");
    expect(plainFromMarkdown("***all three***")).toBe("all three");
  });

  it("unwraps a link to its text — a URL in parentheses is not tidier", () => {
    expect(plainFromMarkdown("Upload to [the portal](https://x.test/p) today.")).toBe(
      "Upload to the portal today.",
    );
    expect(plainFromMarkdown("![our logo](https://x.test/l.png)")).toBe("our logo");
  });

  it("unwraps inline code and heading/quote punctuation", () => {
    expect(plainFromMarkdown("Run `ironjarvis serve` first.")).toBe(
      "Run ironjarvis serve first.",
    );
    expect(plainFromMarkdown("## Next steps")).toBe("Next steps");
    expect(plainFromMarkdown("> quoted")).toBe("quoted");
  });

  it("KEEPS the structure, because that is all plain text has", () => {
    const src = "Hi,\n\n- one\n- two\n\nBest,\nValentino";
    // Bullets and blank lines survive verbatim: strip those and the plain
    // flavour becomes a wall of text, which is the failure being fixed.
    expect(plainFromMarkdown(src)).toBe(src);
  });

  it("leaves prose that only LOOKS like syntax alone", () => {
    // A lone asterisk, snake_case, and arithmetic must not be eaten.
    expect(plainFromMarkdown("file_name_here and 3 * 4 and a_b_c")).toBe(
      "file_name_here and 3 * 4 and a_b_c",
    );
  });

  it("drops the fence markers but keeps what they quoted", () => {
    expect(plainFromMarkdown("see:\n\n```\na **b**\n```")).toBe("see:\n\na **b**");
  });

  it("removes the hard-break padding it no longer needs", () => {
    expect(plainFromMarkdown("Best,  \nValentino")).toBe("Best,\nValentino");
  });
});

/* ------------------------------------------------------ the card gets both --- */

describe("draftFromFence hands the card two finished flavours", () => {
  function pre(markdown: string) {
    // The shape react-markdown gives <pre>: one <code className="language-…">.
    return (
      <code className="language-email">{markdown}</code>
    );
  }

  it("`text` is the plain flavour and `markdown` is the rendered one", () => {
    const body =
      "Subject: S\n\nDue **October 1**. Upload to [the portal](https://x.test).\n\nBest,\nValentino";
    const draft = draftFromFence(pre(body), body);
    expect(draft).not.toBeNull();
    expect(draft!.subject).toBe("S");
    // The plain flavour carries no syntax…
    expect(draft!.text).not.toContain("**");
    expect(draft!.text).not.toContain("](");
    expect(draft!.text).toContain("Due October 1.");
    expect(draft!.text).toContain("Upload to the portal.");
    // …and the rendered one keeps the markdown, with the signature hardened.
    expect(draft!.markdown).toContain("**October 1**");
    expect(draft!.markdown).toContain("Best,  \nValentino");
  });

  it("an ordinary code fence is still code, not a draft", () => {
    const body = "print('hi')";
    expect(draftFromFence(<code className="language-python">{body}</code>, body)).toBeNull();
  });
});

/* ------------------------------------------------------- the whole payload --- */

describe("the payload a real draft produces", () => {
  const REAL = [
    "Hi Dana,",
    "",
    "To finish your return before the **October 15** extended deadline, I need three",
    "things from you:",
    "",
    "- Any K-1s you have received",
    "- Your 1099-B from Schwab",
    "",
    "Please upload them to [the portal](https://x.test/p) by **October 1**.",
    "",
    "Best,",
    "Valentino",
    "Valentino Rossi, CPA",
  ].join("\n");

  it("carries real formatting, point margins, and no stray break", () => {
    const html = htmlPayload(REAL);
    // Bold survives as a tag, not as asterisks.
    expect(html).toContain("<strong>October 15</strong>");
    // Word gives a bare <p> a zero margin, so the spacing has to be inline
    // and in points (v1.163.0) — that rule still holds.
    expect(html).toContain('<p style="margin:0 0 10pt 0;">');
    expect(html).toContain('<ul style="margin:0 0 10pt 0; padding-left:24pt;">');
    // The link is a link.
    expect(html).toContain('href="https://x.test/p"');
    // The ONLY breaks are the signature's.
    expect((html.match(/<br>/g) ?? []).length).toBe(2);
    expect(html).toContain("Valentino<br>");
    // And none of the app's own presentation crossed the clipboard.
    expect(html).not.toContain("class=");
    expect(html).not.toContain("text-zinc");
  });

  it("and its plain twin is readable as it stands", () => {
    const plain = plainFromMarkdown(REAL);
    expect(plain).not.toContain("**");
    expect(plain).not.toContain("](");
    expect(plain).toContain("- Any K-1s you have received");
    expect(plain).toContain("Best,\nValentino\nValentino Rossi, CPA");
  });
});

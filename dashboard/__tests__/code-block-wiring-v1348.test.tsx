/**
 * v1.348.0 polish wave A, INTEGRATION: lane L3's CodeBlock is wired into the
 * ONE markdown renderer (components/Markdown.tsx), and the lanes' follow-ups.
 *
 * - Every ORDINARY fenced block renders through CodeBlock: the same panel, the
 *   same "Copy code" button copying the same raw text, plus colours.
 * - The special fences route exactly as before: a draft is the draft card, a
 *   blank draft is nothing, a chart that parses is the chart card, a chart
 *   that does not is a plain code block.
 * - Inline code keeps its pill (the old in-pre context is gone; a fence never
 *   reaches the inline renderer).
 * - The code block reads the fence's language AS WRITTEN, so `c#` reaches the
 *   highlighter's csharp alias (fenceLang, the draft/chart router, cut it to
 *   "c").
 * - A picture the model wrapped in a LINK keeps the link: the plain picture,
 *   never a zoom button inside an <a> (L4 review follow-up).
 * - Every moving class in the wave's chat files has a reduced-motion variant
 *   (the live "earlier steps" toggle and two older toggles lacked one).
 *
 * Everything runs through the REAL Markdown, the REAL CodeBlock and the REAL
 * lib/highlight (Shiki core + its JavaScript regex engine); only lib/api is
 * a stand-in (no daemon in a test).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({ API_BASE: "http://127.0.0.1:8787", ijToken: () => "tok", post: vi.fn(), ApiError: Error }));

import { Markdown } from "@/components/Markdown";

/** The first colour pass loads Shiki and a grammar for real. */
const LOAD = { timeout: 8_000 };

const COPY_CLASSES =
  "absolute right-2 top-2 z-10 grid h-6 w-6 place-items-center rounded-md border border-white/10 bg-white/[0.06] text-zinc-400 opacity-0 transition-opacity hover:text-zinc-100 focus-visible:opacity-100 group-hover/code:opacity-100";
const PRE_CLASSES =
  "overflow-x-auto rounded border border-white/[0.06] bg-ink-900/80 p-3 font-mono text-xs leading-relaxed text-zinc-200";
const INLINE = "text-[length:calc(1em-1px)]";

const fence = (lang: string, body: string) => "```" + lang + "\n" + body + "\n```\n";

const BAR = JSON.stringify({
  type: "bar",
  title: "Revenue by quarter",
  labels: ["Q1", "Q2", "Q3"],
  series: [{ name: "Revenue", values: [1200, 1500.5, 900] }],
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("an ordinary fence renders through CodeBlock", () => {
  it("the same panel and copy button as before, the raw text inside, and the fence's language", () => {
    const code = "const a = 1;\nexport const b = a + 1;";
    const { container } = render(<Markdown content={"Here:\n\n" + fence("ts", code)} />);
    const block = screen.getByTestId("code-block");
    expect(block.dataset.lang).toBe("typescript");
    expect(block.className).toBe("group/code relative my-2");
    const pre = block.querySelector("pre")!;
    expect(pre.className).toBe(PRE_CLASSES);
    // The block's text is exactly the fence's (no trailing newline added).
    expect(pre.textContent).toBe(code);
    expect(pre.querySelector("code")!.className).toBe("language-ts");
    // One block, one copy button, the old classes (+ reduced motion only).
    const copy = within(block).getByRole("button", { name: "Copy code" });
    expect(copy.getAttribute("title")).toBe("Copy code");
    for (const c of COPY_CLASSES.split(" ")) expect(copy.className.split(" ")).toContain(c);
    expect(container.querySelectorAll("pre")).toHaveLength(1);
  });

  it("Copy writes the raw text, exactly as the old button did", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    const code = "npm install\nnpm run build";
    render(<Markdown content={fence("", code)} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy code" }));
    expect(writeText).toHaveBeenCalledWith(code);
    // Wait for the THING asserted: the spoken confirmation.
    await waitFor(() => expect(screen.getByText("Copied")).toBeTruthy());
  });

  it("a fence in a language we colour is coloured through Markdown (wired end to end)", async () => {
    const code = "print('wired through Markdown')  # v1348 wiring";
    render(<Markdown content={fence("python", code)} />);
    const block = screen.getByTestId("code-block");
    expect(block.dataset.highlighted).toBe("false");
    await waitFor(() => expect(block.dataset.highlighted).toBe("true"), LOAD);
    const spans = [...block.querySelectorAll("pre > code span")].filter((s) => (s as HTMLElement).style.color !== "");
    expect(spans.length).toBeGreaterThan(0);
    // Colours never change the words.
    expect(block.querySelector("pre")!.textContent).toBe(code);
  });

  it("an unlabelled fence is a plain block: no language, never coloured", () => {
    render(<Markdown content={fence("", "just text")} />);
    const block = screen.getByTestId("code-block");
    expect(block.hasAttribute("data-lang")).toBe(false);
    expect(block.querySelector("pre > code")!.hasAttribute("class")).toBe(false);
    expect(block.dataset.highlighted).toBe("false");
  });

  it("`c#` reaches the csharp grammar (the language is read as written)", () => {
    render(<Markdown content={fence("c#", "var x = 1;")} />);
    const block = screen.getByTestId("code-block");
    expect(block.dataset.lang).toBe("csharp");
    expect(block.querySelector("pre > code")!.className).toBe("language-c#");
  });
});

describe("the special fences route exactly as before", () => {
  it("a draft fence is the draft card, not a code block", () => {
    render(<Markdown content={fence("email", "Subject: Q3 close\n\nHi Dana,\n\nAll set.")} />);
    expect(screen.getByTestId("draft-card")).toBeTruthy();
    expect(screen.queryByTestId("code-block")).toBeNull();
  });

  it("a blank draft fence draws nothing at all", () => {
    const { container } = render(<Markdown content={fence("draft", "  ")} />);
    expect(screen.queryByTestId("draft-card")).toBeNull();
    expect(screen.queryByTestId("code-block")).toBeNull();
    expect(container.querySelector("pre")).toBeNull();
  });

  it("a headers-only draft fence is a plain code block (not a card)", () => {
    render(<Markdown content={fence("email", "To: dana@example.com\nSubject: Q3 close")} />);
    expect(screen.queryByTestId("draft-card")).toBeNull();
    const block = screen.getByTestId("code-block");
    expect(block.textContent).toContain("To: dana@example.com");
    expect(block.dataset.highlighted).toBe("false");
  });

  it("a chart fence that parses is the chart card; one that does not is a plain code block", () => {
    render(<Markdown content={fence("chart", BAR)} />);
    expect(screen.getByTestId("chart-card")).toBeTruthy();
    expect(screen.queryByTestId("code-block")).toBeNull();
    cleanup();
    render(<Markdown content={fence("chart", BAR.slice(0, 30))} />);
    expect(screen.queryByTestId("chart-card")).toBeNull();
    const block = screen.getByTestId("code-block");
    expect(block.hasAttribute("data-lang")).toBe(false);
    expect(block.textContent).toBe(BAR.slice(0, 30));
  });

  it("inline code keeps its pill; the fence's code does not get it", () => {
    const { container } = render(<Markdown content={"Run `npm ci` first.\n\n" + fence("", "npm ci")} />);
    const codes = [...container.querySelectorAll("code")];
    expect(codes).toHaveLength(2);
    const inline = codes.find((c) => !c.closest("pre"))!;
    const block = codes.find((c) => c.closest("pre"))!;
    expect(inline.className.split(" ")).toContain(INLINE);
    expect((block.getAttribute("class") ?? "").split(" ")).not.toContain(INLINE);
  });
});

describe("every moving class in the wave's files stops under reduced motion", () => {
  // The brief: every animation stops under prefers-reduced-motion. A class
  // string that moves (transition-*, animate-*) must carry a motion-reduce
  // variant in the SAME string. Read with CRLF normalised (the v1.232.1 rule).
  const FILES = [
    "components/chat/WorkLine.tsx",
    "components/chat/ThinkingDisclosure.tsx",
    "components/chat/CodeBlock.tsx",
    "components/chat/ApprovalPreview.tsx",
    "components/chat/ElicitationCard.tsx",
    "components/MediaLightbox.tsx",
  ];
  it.each(FILES)("%s", (rel) => {
    const src = readFileSync(join(__dirname, "..", rel), "utf8").replace(/\r\n/g, "\n");
    const literals = [...src.matchAll(/"([^"\n]*)"|`([^`]*)`/g)].map((m) => m[1] ?? m[2] ?? "");
    const moving = literals.filter((s) => /(^|\s)(transition(-[a-z]+)?|animate-[a-z-]+)(\s|$)/.test(s));
    // Anti-vacuity: each of these files really has moving classes.
    expect(moving.length, rel).toBeGreaterThan(0);
    for (const s of moving) expect(s, rel).toMatch(/motion-reduce:/);
  });
});

describe("a picture inside a link keeps the link (L4 review follow-up)", () => {
  it("the linked picture is a plain picture inside the link; no zoom button inside an <a>", () => {
    const { container } = render(
      <Markdown content={"[![the chart](C:/work/chart.png)](https://example.com/report)"} />,
    );
    const link = container.querySelector("a")!;
    expect(link.getAttribute("href")).toBe("https://example.com/report");
    const img = link.querySelector("img")!;
    expect(img).toBeTruthy();
    expect(img.getAttribute("alt")).toBe("the chart");
    expect(img.getAttribute("src")).toContain("/creative/file-by-path");
    // The look before v1.348.0, on the picture itself.
    expect(img.className).toBe("my-2 max-h-96 w-auto max-w-full rounded-xl border border-white/10");
    expect(link.querySelector("button")).toBeNull();
    expect(screen.queryByTestId("zoomable-image")).toBeNull();
  });

  it("the same picture with no link still opens full screen", () => {
    render(<Markdown content={"![the chart](C:/work/chart.png)"} />);
    const zoom = screen.getByTestId("zoomable-image");
    expect(zoom.tagName).toBe("BUTTON");
    expect(zoom.closest("a")).toBeNull();
  });
});

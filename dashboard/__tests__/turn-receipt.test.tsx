/**
 * The turn receipt — the accountability strip under an assistant reply.
 *
 * WHAT THESE TESTS ARE REALLY GUARDING is the honesty chip. A mock provider
 * once answered a real chat with a fabricated "Done. Wrote RESULT.md" and
 * nothing in the UI disclosed it. So the load-bearing assertions are:
 *   - the mock/failover/mismatch warning is visible WITHOUT expanding;
 *   - a denied tool shows a count in the COLLAPSED line (a denial hidden
 *     behind the expand would repeat the original bug);
 *   - a document click hands back the FULL absolute path while displaying only
 *     the basename — and basename extraction survives Windows backslashes;
 *   - a turn with nothing to say renders literally nothing (zero-noise).
 */

import { describe, expect, it, vi, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import {
  TurnReceipt,
  docBasename,
  routeWarning,
  type TurnRoute,
} from "@/components/chat/TurnReceipt";

afterEach(() => {
  cleanup();
});

const SERVED_AS_ASKED: TurnRoute = {
  requested: "claude-cli",
  provider: "claude-cli",
  model: "claude-fable-5",
  reason: "explicit",
};

function expand() {
  fireEvent.click(screen.getByRole("button", { expanded: false }));
}

describe("TurnReceipt — collapsed line", () => {
  it("renders literally nothing when there is nothing to say", () => {
    const { container } = render(
      <TurnReceipt
        route={null}
        toolsUsed={[]}
        deniedTools={[]}
        documents={[]}
        // usage alone is not an accountability fact worth a strip
        usage={{ input_tokens: 12, output_tokens: 34 }}
        contextPct={0.5}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("also renders nothing when every prop is absent", () => {
    const { container } = render(<TurnReceipt />);
    expect(container.firstChild).toBeNull();
  });

  it("shows a quiet one-line summary: provider, tool count, file count", () => {
    render(
      <TurnReceipt
        route={{ provider: "claude-cli", reason: "default" }}
        toolsUsed={["read_file", "write_document", "shell"]}
        documents={["C:/w/a.md", "C:/w/b.md"]}
      />,
    );
    const toggle = screen.getByRole("button", { expanded: false });
    expect(toggle.textContent).toContain("claude-cli");
    expect(toggle.textContent).toContain("3 tools");
    expect(toggle.textContent).toContain("2 files");
    // Served-as-asked: no warning styling and no alarming wording.
    expect(screen.queryByText(/answered by/)).toBeNull();
    expect(screen.queryByText(/mock answer/)).toBeNull();
    expect(document.querySelector(".text-amber-300")).toBeNull();
  });

  it("singularizes: 1 tool, 1 file", () => {
    render(
      <TurnReceipt
        route={{ provider: "ollama", reason: "default" }}
        toolsUsed={["repl"]}
        documents={["/tmp/out.txt"]}
      />,
    );
    const toggle = screen.getByRole("button", { expanded: false });
    expect(toggle.textContent).toContain("1 tool");
    expect(toggle.textContent).not.toContain("1 tools");
    expect(toggle.textContent).toContain("1 file");
    expect(toggle.textContent).not.toContain("1 files");
  });
});

describe("TurnReceipt — the honesty chip (visible WITHOUT expanding)", () => {
  it("mock gets the strongest wording, collapsed", () => {
    render(<TurnReceipt route={{ provider: "mock", reason: "default" }} />);
    // No expand click — this must be on the collapsed line.
    const chip = screen.getByText(/mock answer — no real model ran/);
    expect(chip).toBeTruthy();
    expect(screen.getByRole("button", { expanded: false })).toBeTruthy();
    // And it is styled as a warning, not quiet zinc.
    expect(chip.className).toContain("amber");
  });

  it("failover names who actually answered, collapsed, amber", () => {
    render(
      <TurnReceipt
        route={{
          requested: "claude-cli",
          provider: "openai",
          reason: "failover",
        }}
      />,
    );
    const chip = screen.getByText(/answered by openai — failover/);
    expect(chip.className).toContain("amber");
  });

  it("requested !== provider warns even without a failover reason", () => {
    render(
      <TurnReceipt
        route={{ requested: "ollama", provider: "openai", reason: "explicit" }}
      />,
    );
    const chip = screen.getByText(
      /answered by openai — asked for ollama/,
    );
    expect(chip.className).toContain("amber");
  });

  it("served-as-asked stays quiet: provider name, no warning", () => {
    render(<TurnReceipt route={SERVED_AS_ASKED} />);
    const toggle = screen.getByRole("button", { expanded: false });
    expect(toggle.textContent).toContain("claude-cli");
    expect(screen.queryByText(/answered by/)).toBeNull();
    expect(document.querySelector(".text-amber-300")).toBeNull();
  });
});

describe("TurnReceipt — denied tools", () => {
  it("shows the blocked count in the COLLAPSED line", () => {
    render(
      <TurnReceipt route={SERVED_AS_ASKED} deniedTools={["shell"]} />,
    );
    // No expand — a silent denial invisible until expand is the old bug.
    const toggle = screen.getByRole("button", { expanded: false });
    expect(toggle.textContent).toContain("1 blocked");
  });

  it("expanded, each denial reads as a warning: 'blocked: shell'", () => {
    render(
      <TurnReceipt
        route={SERVED_AS_ASKED}
        deniedTools={["shell", "write_file"]}
      />,
    );
    expand();
    const shell = screen.getByText("blocked: shell");
    expect(shell.className).toContain("amber");
    expect(screen.getByText("blocked: write_file")).toBeTruthy();
  });
});

describe("TurnReceipt — expand/collapse", () => {
  it("toggle is a button with aria-expanded that flips on click", () => {
    render(<TurnReceipt route={SERVED_AS_ASKED} toolsUsed={["repl"]} />);
    const toggle = screen.getByRole("button", { expanded: false });
    expect(toggle.tagName).toBe("BUTTON");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
  });

  it("expanded shows requested vs served + reason, tools, usage, context %", () => {
    render(
      <TurnReceipt
        route={{
          requested: "claude-cli",
          provider: "openai",
          model: "gpt-5.2",
          reason: "failover",
        }}
        toolsUsed={["read_file", "repl"]}
        usage={{ input_tokens: 1234, output_tokens: 567 }}
        contextPct={0.42}
      />,
    );
    // Detail hidden while collapsed.
    expect(screen.queryByText("read_file")).toBeNull();
    expand();
    // Requested vs served + reason.
    expect(screen.getByText(/requested claude-cli/)).toBeTruthy();
    expect(screen.getByText(/\(failover\)/)).toBeTruthy();
    expect(screen.getByText(/gpt-5\.2/)).toBeTruthy();
    // Tools as individual entries.
    expect(screen.getByText("read_file")).toBeTruthy();
    expect(screen.getByText("repl")).toBeTruthy();
    // Token usage + context pressure as a percentage.
    expect(screen.getByText(/1,234 in/)).toBeTruthy();
    expect(screen.getByText(/567 out/)).toBeTruthy();
    expect(screen.getByText(/context 42%/)).toBeTruthy();
  });
});

describe("TurnReceipt — documents", () => {
  it("doc click calls onOpenDocument with the FULL path, displays basename", () => {
    const onOpen = vi.fn();
    const full = "C:/Users/VR/.ironjarvis/uploads/summary.docx";
    render(
      <TurnReceipt
        route={SERVED_AS_ASKED}
        documents={[full]}
        onOpenDocument={onOpen}
      />,
    );
    expand();
    const doc = screen.getByText("summary.docx");
    // Accessible: a button, not a div — and the full path is discoverable.
    expect(doc.tagName).toBe("BUTTON");
    expect(doc.getAttribute("title")).toBe(full);
    fireEvent.click(doc);
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onOpen).toHaveBeenCalledWith(full);
  });

  it("Windows backslash path renders its basename", () => {
    render(
      <TurnReceipt
        route={SERVED_AS_ASKED}
        documents={["C:\\Users\\VR\\x\\report.pdf"]}
      />,
    );
    expand();
    const doc = screen.getByText("report.pdf");
    expect(doc.getAttribute("title")).toBe("C:\\Users\\VR\\x\\report.pdf");
  });

  it("survives a missing onOpenDocument (click is a no-op, not a crash)", () => {
    render(<TurnReceipt route={SERVED_AS_ASKED} documents={["/a/b.txt"]} />);
    expand();
    fireEvent.click(screen.getByText("b.txt"));
    expect(screen.getByText("b.txt")).toBeTruthy();
  });
});

describe("docBasename", () => {
  it("handles forward slashes", () => {
    expect(docBasename("/home/vr/notes/plan.md")).toBe("plan.md");
  });
  it("handles backslashes", () => {
    expect(docBasename("C:\\Users\\VR\\x\\report.pdf")).toBe("report.pdf");
  });
  it("handles mixed separators and trailing separators", () => {
    expect(docBasename("C:\\Users\\VR/x/report.pdf")).toBe("report.pdf");
    expect(docBasename("C:/Users/VR/x/")).toBe("x");
  });
  it("falls back to the raw string for a bare name", () => {
    expect(docBasename("report.pdf")).toBe("report.pdf");
  });
});

describe("routeWarning (the pure honesty predicate)", () => {
  it("mock outranks everything", () => {
    expect(
      routeWarning({ requested: "mock", provider: "mock", reason: "explicit" }),
    ).toBe("mock answer — no real model ran");
  });
  it("mock outranks failover — a failover TO the mock is still a mock answer", () => {
    // Kills the precedence-swap mutation: reason says failover, but "no real
    // model ran" is the stronger (and truer) claim.
    expect(
      routeWarning({
        requested: "claude-cli",
        provider: "mock",
        reason: "failover",
      }),
    ).toBe("mock answer — no real model ran");
  });
  it("failover warns even when requested is absent", () => {
    expect(routeWarning({ provider: "openai", reason: "failover" })).toContain(
      "failover",
    );
  });
  it("failover names who was asked for when that is known", () => {
    // The router's failover path sets requested = the explicit pick (or "").
    expect(
      routeWarning({
        requested: "claude-cli",
        provider: "openai",
        reason: "failover",
      }),
    ).toBe("answered by openai — failover from claude-cli");
  });
  it("failover with requested === provider still warns, without a bogus 'from'", () => {
    expect(
      routeWarning({ requested: "openai", provider: "openai", reason: "failover" }),
    ).toBe("answered by openai — failover");
  });
  it('requested "" (chat\'s normal default-route value) is "didn\'t ask", not a mismatch', () => {
    expect(
      routeWarning({ requested: "", provider: "ollama", reason: "default" }),
    ).toBeNull();
    expect(
      routeWarning({ requested: "", provider: "openai", reason: "failover" }),
    ).toBe("answered by openai — failover");
  });
  it("the quiet reasons stay quiet when the asked-for provider served", () => {
    // "prompted-tools" = the CHOSEN adapter kept the request via the scaffold
    // (same provider serves — a capability REROUTE is labelled "failover" by
    // the router itself). "auto-tier"/"local-oracle" are the user's own
    // configured automation, not a substitution.
    expect(routeWarning(SERVED_AS_ASKED)).toBeNull();
    expect(routeWarning({ provider: "ollama", reason: "default" })).toBeNull();
    expect(
      routeWarning({
        requested: "ollama",
        provider: "ollama",
        reason: "prompted-tools",
      }),
    ).toBeNull();
    expect(routeWarning({ provider: "openai", reason: "auto-tier" })).toBeNull();
    expect(
      routeWarning({ provider: "ollama", reason: "local-oracle" }),
    ).toBeNull();
    expect(routeWarning(null)).toBeNull();
    expect(routeWarning(undefined)).toBeNull();
  });
  it("a quiet reason does NOT suppress a requested/served mismatch", () => {
    expect(
      routeWarning({
        requested: "claude-cli",
        provider: "openai",
        reason: "prompted-tools",
      }),
    ).toBe("answered by openai — asked for claude-cli");
  });
});

describe("TurnReceipt — pathological server data", () => {
  it("a degenerate route ({} / empty provider) with nothing else renders nothing", () => {
    const { container } = render(
      <TurnReceipt
        route={{ requested: "", provider: "", model: "", reason: "" }}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("empty/whitespace-only tool names do not defeat the zero-noise guard", () => {
    const { container } = render(
      <TurnReceipt toolsUsed={["", "  "]} deniedTools={[""]} documents={[""]} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("blank entries are not counted alongside real ones", () => {
    render(
      <TurnReceipt
        route={SERVED_AS_ASKED}
        toolsUsed={["read_file", "", "  "]}
      />,
    );
    const toggle = screen.getByRole("button", { expanded: false });
    expect(toggle.textContent).toContain("1 tool");
    expect(toggle.textContent).not.toContain("3 tool");
  });

  it("NaN contextPct renders no context figure and no dangling separator", () => {
    render(
      <TurnReceipt
        route={SERVED_AS_ASKED}
        usage={{ input_tokens: 1234 }}
        contextPct={Number.NaN}
      />,
    );
    expand();
    expect(screen.queryByText(/NaN/)).toBeNull();
    const gaugeText = screen.getByText(/1,234 in/);
    expect(gaugeText.textContent).toBe("1,234 in");
  });

  it("negative contextPct is unreportable, not a negative percentage", () => {
    render(<TurnReceipt route={SERVED_AS_ASKED} contextPct={-0.2} />);
    expand();
    expect(screen.queryByText(/context/)).toBeNull();
    expect(screen.queryByText(/-20/)).toBeNull();
  });

  it("contextPct 0 is a real figure and renders", () => {
    render(<TurnReceipt route={SERVED_AS_ASKED} contextPct={0} />);
    expand();
    expect(screen.getByText(/context 0%/)).toBeTruthy();
  });

  it("usage with only one finite field renders just that field", () => {
    render(
      <TurnReceipt
        route={SERVED_AS_ASKED}
        usage={{ input_tokens: Number.NaN, output_tokens: 567 }}
      />,
    );
    expand();
    expect(screen.queryByText(/NaN/)).toBeNull();
    expect(screen.getByText(/567 out/).textContent).toBe("567 out");
  });

  it("a duplicated document path is ONE file: one count, one chip", () => {
    const p = "C:/w/report.md";
    render(<TurnReceipt route={SERVED_AS_ASKED} documents={[p, p]} />);
    const toggle = screen.getByRole("button", { expanded: false });
    expect(toggle.textContent).toContain("1 file");
    expand();
    expect(screen.getAllByText("report.md")).toHaveLength(1);
  });

  it("identical basenames from DIFFERENT folders both render and click correctly", () => {
    const onOpen = vi.fn();
    const a = "C:/w/one/notes.md";
    const b = "C:/w/two/notes.md";
    render(
      <TurnReceipt
        route={SERVED_AS_ASKED}
        documents={[a, b]}
        onOpenDocument={onOpen}
      />,
    );
    expand();
    const chips = screen.getAllByText("notes.md");
    expect(chips).toHaveLength(2);
    fireEvent.click(chips[0]);
    fireEvent.click(chips[1]);
    expect(onOpen).toHaveBeenNthCalledWith(1, a);
    expect(onOpen).toHaveBeenNthCalledWith(2, b);
  });

  it("a 300-char tool name is truncated in the chip with the full name on title", () => {
    const long = "x".repeat(300);
    render(<TurnReceipt route={SERVED_AS_ASKED} toolsUsed={[long]} />);
    expand();
    const chip = screen.getByTitle(long);
    expect(chip.textContent).toBe(long);
    expect(chip.className).toContain("truncate");
  });
});

describe("TurnReceipt — interaction & accessibility", () => {
  it("clicking the warning chip itself toggles expansion (it lives inside the button)", () => {
    render(<TurnReceipt route={{ provider: "mock", reason: "mock" }} />);
    fireEvent.click(screen.getByText(/mock answer/));
    expect(
      screen.getByRole("button", { expanded: true }).getAttribute("aria-expanded"),
    ).toBe("true");
  });

  it("the toggle is wired to the expanded panel via aria-controls/id", () => {
    render(<TurnReceipt route={SERVED_AS_ASKED} toolsUsed={["read_file"]} />);
    const toggle = screen.getByRole("button", { expanded: false });
    // Collapsed: no panel exists, so no dangling aria-controls reference.
    expect(toggle.getAttribute("aria-controls")).toBeNull();
    fireEvent.click(toggle);
    const id = toggle.getAttribute("aria-controls");
    expect(id).toBeTruthy();
    const panel = document.getElementById(id as string);
    expect(panel).not.toBeNull();
    expect(panel?.textContent).toContain("read_file");
  });

  it("no illegal DOM nesting: doc buttons are OUTSIDE the toggle button", () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      render(
        <TurnReceipt
          route={{ requested: "a", provider: "b", reason: "failover" }}
          toolsUsed={["repl"]}
          deniedTools={["shell"]}
          documents={["C:/w/a.md"]}
          usage={{ input_tokens: 1, output_tokens: 2 }}
          contextPct={0.5}
        />,
      );
      expand();
      const nesting = errors.mock.calls
        .flat()
        .join(" ");
      expect(nesting).not.toMatch(/cannot (appear|be a descendant)|validateDOMNesting/);
      // And structurally: the toggle contains no nested interactive element.
      const toggle = screen.getByRole("button", { expanded: true });
      expect(toggle.querySelector("button, a")).toBeNull();
    } finally {
      errors.mockRestore();
    }
  });

  it("a route with a warning but NOTHING else must never be swallowed by the null-render guard", () => {
    // Route present, provider mock, zero tools/denials/docs — the exact shape
    // of the motivating incident. It MUST render, collapsed, amber.
    const { container } = render(
      <TurnReceipt
        route={{ requested: "", provider: "mock", reason: "mock" }}
        toolsUsed={[]}
        deniedTools={[]}
        documents={[]}
      />,
    );
    expect(container.firstChild).not.toBeNull();
    expect(screen.getByText(/mock answer — no real model ran/)).toBeTruthy();
    expect(screen.getByRole("button", { expanded: false })).toBeTruthy();
  });
});

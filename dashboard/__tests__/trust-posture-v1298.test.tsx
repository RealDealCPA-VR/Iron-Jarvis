/**
 * v1.298.0 (wave 4a) — TRUST posture + context BLOCKED notices.
 *
 * A run is in LOW trust when it started from an unattended inbound message
 * or read content flagged as injection mid-run; under low trust it cannot
 * change memory, settings, agents or skills. Every wire field is OPTIONAL
 * (the daemon doers add them concurrently), so the pinned contract is
 * two-sided: a low-trust row wears the chip / banner / receipt line, and a
 * full-trust or untagged row renders EXACTLY today's look — the
 * anti-vacuity half of each test is the one that keeps a "trust" badge
 * from landing on every card.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ShieldAlert, ShieldCheck } from "lucide-react";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    responses: {} as Record<string, unknown>,
    puts: [] as { path: string; body: unknown }[],
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    const r = api.responses[path];
    if (r === undefined) {
      return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
    }
    return Promise.resolve(r);
  },
  post: () => Promise.resolve({}),
  put: (path: string, body?: unknown) => {
    api.puts.push({ path, body });
    return Promise.resolve({});
  },
  del: () => Promise.resolve({}),
}));
// The settings page (comm_trust control) — its sibling cards are not under test.
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ refresh: () => {}, health: null, online: true }),
}));
vi.mock("@/components/settings/MaintenanceTools", () => ({ MaintenanceTools: () => null }));
vi.mock("@/components/settings/DaemonTokenCard", () => ({ DaemonTokenCard: () => null }));

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? api.responses[path] ?? null : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? api.responses[path] ?? null : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({
    text: "",
    tools: [],
    phase: null,
    active: false,
    start: () => {},
    stop: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [] }) }));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    enabled: false,
    supported: false,
    toggle: () => {},
    speak: () => {},
    stop: () => {},
  }),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: () => {} }) }));
vi.mock("@/components/ReviewPanel", () => ({ ReviewPanel: () => null }));
vi.mock("@/components/TracesPanel", () => ({ TracesPanel: () => null }));
vi.mock("@/components/SessionFeedback", () => ({ SessionFeedback: () => null }));
vi.mock("@/components/TimeTravelFeed", () => ({ TimeTravelFeed: () => null }));
vi.mock("@/components/chat/DocPreview", () => ({
  DocPreview: ({ path }: { path: string }) => <div data-testid="doc-preview">{path}</div>,
  appLabelFor: () => "app",
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({
      href,
      children,
      ...rest
    }: {
      href: string;
      children?: React.ReactNode;
    }) => createElement("a", { href, ...rest }, children),
  };
});
vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set([
    "initial",
    "animate",
    "exit",
    "transition",
    "variants",
    "layout",
    "whileHover",
    "whileTap",
    "whileInView",
    "viewport",
  ]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) {
      if (!MOTION_ONLY.has(k)) rest[k] = v;
    }
    return createElement(tag, rest);
  };
  const cache = new Map<string, unknown>();
  return {
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: React.ReactNode }) =>
      createElement(Fragment, null, children),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => {
        const key = String(tag);
        if (!cache.has(key)) cache.set(key, tagFor(key));
        return cache.get(key);
      },
    }),
  };
});

import TrustChip from "@/components/TrustChip";
import { CardInner } from "@/components/kanban/SessionCard";
import SessionDetailPage from "@/app/sessions/[id]/page";
import { TurnReceipt, blockedRows } from "@/components/chat/TurnReceipt";
import { toActivity } from "@/components/NotificationBell";
import SettingsPage from "@/app/settings/page";
import type { SessionView } from "@/lib/types";

afterEach(() => {
  cleanup();
  api.responses = {};
  api.puts = [];
});

const REASON = "started from an inbound email message";
const TAINT_REASON = "read content flagged as injection from web_fetch";

function sv(over: Partial<SessionView> = {}): SessionView {
  return {
    id: "s-1",
    task: "triage the inbox",
    agent_type: "coder",
    provider: "mock",
    model: "mock-model",
    status: "running",
    workspace_path: "C:/w",
    summary: "",
    created_at: "2026-10-01T10:00:00Z",
    finished_at: null,
    ...over,
  };
}

/* ------------------------------------------------------------ TrustChip */

describe("TrustChip", () => {
  it("renders nothing for full trust and for an untagged row", () => {
    const { container, rerender } = render(<TrustChip trust="full" reason="x" />);
    expect(container).toBeEmptyDOMElement();
    rerender(<TrustChip />);
    expect(container).toBeEmptyDOMElement();
    rerender(<TrustChip trust={null} reason={null} taintedAt={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("says low trust with the daemon's reason as its title", () => {
    render(<TrustChip trust="low" reason={REASON} />);
    const chip = screen.getByTestId("trust-chip");
    expect(chip).toHaveTextContent("low trust");
    expect(chip).not.toHaveTextContent("since");
    expect(chip).toHaveAttribute("title", REASON);
    expect(chip).not.toHaveAttribute("data-tainted");
  });

  it("a tainted run says since when", () => {
    render(<TrustChip trust="low" reason={TAINT_REASON} taintedAt="2026-10-01T10:05:00Z" />);
    const chip = screen.getByTestId("trust-chip");
    expect(chip).toHaveTextContent(/low trust.*since \d/);
    expect(chip).toHaveAttribute("data-tainted", "true");
    expect(chip.getAttribute("title")).toMatch(new RegExp(`^${TAINT_REASON} — since \\d`));
  });
});

/* ---------------------------------------------------------- SessionCard */

describe("SessionCard wears the chip beside the origin", () => {
  it("a low-trust row shows it", () => {
    render(
      <CardInner
        session={sv({ origin: "comm:email", trust: "low", trust_reason: REASON })}
        lane="active"
      />,
    );
    expect(screen.getByTestId("origin-chip")).toBeInTheDocument();
    const chip = screen.getByTestId("trust-chip");
    expect(chip).toHaveAttribute("title", REASON);
  });

  it("a full-trust row and an untagged row do not", () => {
    render(<CardInner session={sv({ trust: "full", trust_reason: "" })} lane="active" />);
    expect(screen.queryByTestId("trust-chip")).not.toBeInTheDocument();
    cleanup();
    render(<CardInner session={sv()} lane="active" />);
    expect(screen.queryByTestId("trust-chip")).not.toBeInTheDocument();
  });
});

/* --------------------------------------------------------- session page */

function fakeParams(id: string): Promise<{ id: string }> {
  const p = Promise.resolve({ id });
  Object.assign(p as object, { status: "fulfilled", value: { id } });
  return p;
}

function renderPage(over: Partial<SessionView>) {
  api.responses["/sessions/s-1"] = {
    session: sv(over),
    transcript: { runs: [], tools: [] },
  };
  return render(<SessionDetailPage params={fakeParams("s-1")} />);
}

describe("sessions/[id] — the low-trust banner", () => {
  it("one line under the header, with the reason and what it cannot change", async () => {
    renderPage({ trust: "low", trust_reason: REASON });
    await act(async () => {});
    const banner = screen.getByTestId("trust-banner");
    expect(banner).toHaveTextContent(
      `This run is in low trust — ${REASON}. It cannot change memory, settings, agents or skills.`,
    );
    expect(screen.getAllByTestId("trust-banner")).toHaveLength(1);
    // The header chip rides along too.
    expect(screen.getByTestId("trust-chip")).toBeInTheDocument();
  });

  it("no banner and no chip on a full-trust run, nor on an untagged one", async () => {
    renderPage({ trust: "full", trust_reason: "" });
    await act(async () => {});
    expect(screen.queryByTestId("trust-banner")).not.toBeInTheDocument();
    expect(screen.queryByTestId("trust-chip")).not.toBeInTheDocument();
    cleanup();
    renderPage({});
    await act(async () => {});
    expect(screen.queryByTestId("trust-banner")).not.toBeInTheDocument();
    expect(screen.queryByTestId("trust-chip")).not.toBeInTheDocument();
  });
});

/* ---------------------------------------------------------- TurnReceipt */

describe("TurnReceipt — trust + blocked on the quiet line", () => {
  it("absent fields render nothing at all (the zero-noise guard holds)", () => {
    const { container } = render(<TurnReceipt />);
    expect(container).toBeEmptyDOMElement();
    cleanup();
    // Present-but-empty is the same as absent: full trust, no rows.
    const r2 = render(<TurnReceipt trust="full" trustReason="" blocked={[]} />);
    expect(r2.container).toBeEmptyDOMElement();
    expect(screen.queryByTestId("turn-trust")).not.toBeInTheDocument();
    expect(screen.queryByTestId("turn-context-blocked")).not.toBeInTheDocument();
  });

  it("low trust is on the collapsed line with the reason, without expanding", () => {
    render(<TurnReceipt trust="low" trustReason={REASON} />);
    const line = screen.getByTestId("turn-trust");
    expect(line).toHaveTextContent(`low trust: ${REASON}`);
    expect(screen.getByRole("button", { expanded: false })).toContainElement(line);
  });

  it("blocked passages are counted per source on the line", () => {
    render(
      <TurnReceipt
        toolsUsed={["web_fetch"]}
        blocked={[
          { source: "web_fetch", count: 2 },
          { source: "email", count: 1 },
          { source: "noop", count: 0 },
        ]}
      />,
    );
    const rows = screen.getAllByTestId("turn-context-blocked");
    expect(rows.map((r) => r.textContent)).toEqual([
      "2 blocked from web_fetch",
      "1 blocked from email",
    ]);
    expect(screen.queryByTestId("turn-trust")).not.toBeInTheDocument();
  });

  it("blockedRows drops shapeless rows and names an unnamed source", () => {
    expect(
      blockedRows([
        { source: "x", count: 0 },
        { count: 3 },
        null as unknown as { source: string },
        { source: " tool ", count: 1.9 },
      ]),
    ).toEqual([
      { source: "context", count: 3 },
      { source: "tool", count: 1 },
    ]);
    expect(blockedRows(undefined)).toEqual([]);
    expect(blockedRows(null)).toEqual([]);
  });
});

/* ------------------------------------------- the wire: done frame → receipt */

describe("the done frame's trust fields reach the receipt (v1.298.0)", () => {
  it("decodeSSE whitelists trust / trust_reason / trust_note, absent when the frame has none", async () => {
    const { decodeSSE } = await vi.importActual<typeof import("@/lib/useChatStream")>(
      "@/lib/useChatStream",
    );
    const ev = decodeSSE(
      "done",
      JSON.stringify({
        reply: "r",
        trust: "low",
        trust_reason: REASON,
        trust_note: "low trust: 4 tools kept away",
      }),
    ) as { trust?: string; trust_reason?: string; trust_note?: string };
    expect(ev.trust).toBe("low");
    expect(ev.trust_reason).toBe(REASON);
    expect(ev.trust_note).toBe("low trust: 4 tools kept away");
    // Anti-vacuity: no fields on the wire → no keys on the event.
    const none = decodeSSE("done", JSON.stringify({ reply: "r" })) as Record<string, unknown>;
    expect("trust" in none).toBe(false);
    expect("trust_reason" in none).toBe(false);
    expect("trust_note" in none).toBe(false);
    // Non-strings are not strings.
    const junk = decodeSSE(
      "done",
      JSON.stringify({ reply: "r", trust: 3, trust_reason: null, trust_note: "" }),
    ) as Record<string, unknown>;
    expect("trust" in junk).toBe(false);
    expect("trust_note" in junk).toBe(false);
  });

  it("the real hook carries the three onto the resolved result", async () => {
    const { useChatStream } = await vi.importActual<typeof import("@/lib/useChatStream")>(
      "@/lib/useChatStream",
    );
    const { renderHook } = await import("@testing-library/react");
    const frames = `event: done\ndata: ${JSON.stringify({
      reply: "ok",
      trust: "low",
      trust_reason: REASON,
      trust_note: "low trust: 4 tools kept away",
    })}\n\n`;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(frames));
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(stream, { status: 200 })));
    try {
      const { result } = renderHook(() => useChatStream());
      let settled: { trust?: string; trustReason?: string; trustNote?: string } | null = null;
      await act(async () => {
        settled = (await result.current.run({ messages: [] })) as typeof settled;
      });
      expect(settled!.trust).toBe("low");
      expect(settled!.trustReason).toBe(REASON);
      expect(settled!.trustNote).toBe("low trust: 4 tools kept away");
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("the receipt says the note beside the reason, once", () => {
    render(<TurnReceipt trust="low" trustReason={REASON} trustNote="low trust: 4 tools kept away" />);
    expect(screen.getByTestId("turn-trust").textContent).toBe(
      `low trust: ${REASON} — 4 tools kept away`,
    );
    cleanup();
    render(<TurnReceipt trust="low" trustNote="4 tools kept away" />);
    expect(screen.getByTestId("turn-trust").textContent).toBe("low trust — 4 tools kept away");
    cleanup();
    // A note alone, under full trust, is NOT a line.
    const { container } = render(<TurnReceipt trust="full" trustNote="low trust: x" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("both lanes of the chat page and the Build pane store it and pass it (source pin)", async () => {
    // The merge lines are where done-frame fields have died silently before
    // (denied_tools, doors — CLAUDE.md). Read with CRLF normalised: CI checks
    // out with autocrlf.
    const { readFileSync } = await import("node:fs");
    const read = (p: string) => readFileSync(p, "utf8").replace(/\r\n/g, "\n");
    const page = read("app/chat/page.tsx");
    const at = page.indexOf("const receipt = {");
    expect(at).toBeGreaterThan(-1);
    const stream = page.slice(at, at + 1200);
    expect(stream).toMatch(/trust === "low"[\s\S]*?trustReason[\s\S]*?trustNote/);
    const post = page.slice(page.indexOf("const receiptPost = {"), page.indexOf("const receiptPost = {") + 1200);
    expect(post).toMatch(/res\.trust === "low"[\s\S]*?trust_reason[\s\S]*?trust_note/);
    expect(page.match(/trust=\{m\.trust\}\s+trustReason=\{m\.trustReason\}\s+trustNote=\{m\.trustNote\}/g)).toHaveLength(1);
    const pane = read("components/terminal/PaneChat.tsx");
    expect(pane).toMatch(/res\.trust === "low"[\s\S]*?trustReason[\s\S]*?trustNote/);
    expect(pane.match(/trust=\{m\.trust\}\s+trustReason=\{m\.trustReason\}\s+trustNote=\{m\.trustNote\}/g)).toHaveLength(1);
  });
});

/* ------------------------------------------------------------------ bell */

describe("the bell maps trust.lowered and context.blocked", () => {
  it("trust.lowered → the run's page, ShieldAlert, the reason as body", () => {
    const item = toActivity({
      id: "evt_1",
      type: "trust.lowered",
      ts: "2026-10-01T10:05:00Z",
      session_id: null,
      payload: {
        session_id: "session_abc",
        tool: "web_fetch",
        category: "injection",
        reason: TAINT_REASON,
      },
    });
    expect(item).not.toBeNull();
    expect(item!.href).toBe("/sessions/session_abc");
    expect(item!.icon).toBe(ShieldAlert);
    expect(item!.title).toBe("A run dropped to low trust");
    expect(item!.body).toBe(TAINT_REASON);
  });

  it("context.blocked → the run's page, ShieldCheck, a counted title, categories as body", () => {
    const item = toActivity({
      id: "evt_2",
      type: "context.blocked",
      ts: "2026-10-01T10:05:00Z",
      session_id: "session_abc",
      payload: {
        source: "web_fetch",
        count: 3,
        categories: ["injection", "exfiltration"],
      },
    });
    expect(item).not.toBeNull();
    expect(item!.href).toBe("/sessions/session_abc");
    expect(item!.icon).toBe(ShieldCheck);
    expect(item!.title).toBe("Blocked 3 suspicious passages from web_fetch");
    expect(item!.body).toBe("injection, exfiltration");
  });

  it("a chat-lane event (session_id \"chat\", no row) links to /chat, never /sessions/chat", () => {
    // Both chat lanes publish their taint and their blocked passages with
    // session_id "chat" (a chat turn has no Session row): a bell link to
    // /sessions/chat would open an empty page.
    const lowered = toActivity({
      id: "evt_c1",
      type: "trust.lowered",
      ts: "2026-10-01T10:05:00Z",
      session_id: "chat",
      payload: { session_id: "chat", tool: "web_fetch", category: "injection", reason: TAINT_REASON },
    });
    expect(lowered!.href).toBe("/chat");
    expect(lowered!.title).toBe("A chat turn dropped to low trust");
    expect(lowered!.body).toBe(TAINT_REASON);
    const blocked = toActivity({
      id: "evt_c2",
      type: "context.blocked",
      ts: "2026-10-01T10:05:00Z",
      session_id: "chat",
      payload: { session_id: "chat", source: "attachment notes.txt", count: 2, categories: ["x"] },
    });
    expect(blocked!.href).toBe("/chat");
    expect(blocked!.title).toBe("Blocked 2 suspicious passages from attachment notes.txt");
  });

  it("context.blocked with no session goes to /activity and counts one passage singular", () => {
    const item = toActivity({
      id: "evt_3",
      type: "context.blocked",
      ts: "2026-10-01T10:05:00Z",
      session_id: null,
      payload: { source: "email", count: 1, categories: [] },
    });
    expect(item!.href).toBe("/activity");
    expect(item!.title).toBe("Blocked 1 suspicious passage from email");
    expect(item!.body).toBe("");
  });
});

/* ------------------------------------------------- settings: comm_trust */

describe("Settings — runs started from inbound messages (comm_trust)", () => {
  const LABEL = "Runs started from inbound messages (phone, Slack, email)";

  async function renderSettings(comm_trust: string) {
    api.responses["/settings"] = { settings: { comm_trust } };
    render(<SettingsPage />);
    return (await screen.findByLabelText(LABEL)) as HTMLSelectElement;
  }

  it("renders the saved value, with plain-word options over the daemon's tokens", async () => {
    const sel = await renderSettings("full");
    expect(sel.value).toBe("full");
    const labels = Array.from(sel.options).map((o) => [o.value, o.textContent]);
    expect(labels).toEqual([
      ["low", "Low trust (default) — cannot change memory, settings, agents or skills"],
      ["full", "Full trust"],
    ]);
    cleanup();
    const sel2 = await renderSettings("low");
    expect(sel2.value).toBe("low");
  });

  it("changing it PUTs comm_trust through the page's one save, and nothing else", async () => {
    const sel = await renderSettings("low");
    fireEvent.change(sel, { target: { value: "full" } });
    expect(sel.value).toBe("full");
    expect(api.puts).toEqual([]); // the page saves on Save, not per keystroke
    fireEvent.click(screen.getByRole("button", { name: /^save/i }));
    await waitFor(() => expect(api.puts).toHaveLength(1));
    expect(api.puts[0]).toEqual({ path: "/settings", body: { values: { comm_trust: "full" } } });
    await screen.findByText(/Saved 1 setting\./);
  });
});

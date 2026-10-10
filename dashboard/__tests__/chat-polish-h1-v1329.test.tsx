/**
 * v1.329.0 (calm chat W6 H1) — the chat page's last loud spots, made calm.
 *
 *  (1) In a project chat the sidebar's chat list starts like it does off
 *      /chat: one quiet search, then the filter as a quiet pair of ghosts
 *      ("All chats" / "Only <project>", the one in effect filled), never an
 *      uppercase "ALL CHATS" label or an underlined link.
 *  (2) Same-titled rows show ONE quiet second part (the short time or the
 *      day) in place of the age, never "6:11:46 PM 5h"; the exact time is on
 *      hover.
 *  (3) A failover or mismatch receipt names the MODEL that answered, as a
 *      normal row does; the provider stays in the chip's title and the
 *      expanded detail.
 *  (4) The expanded receipt's file is a plain row in the normal font.
 *  (5) "Jump to latest" is a calm ghost chip: a hairline, an ink fill, no
 *      accent border, no glow.
 *  (6) Tasks and Media read in the chat's centred column; the Board keeps
 *      its width for its columns.
 */

import { type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  class FakeStreamError extends FakeApiError {
    committed = false;
    offline = false;
    partial = "";
  }
  return {
    FakeApiError,
    FakeStreamError,
    nav: { pathname: "/chat" },
    gets: [] as string[],
    responses: {} as Record<string, unknown>,
  };
});

vi.mock("next/navigation", () => ({
  usePathname: () => H.nav.pathname,
  useRouter: () => ({ push: () => {}, replace: () => {}, prefetch: () => {} }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: { children: ReactNode; href: string } & Record<string, unknown>) => (
    <a href={href} {...(rest as Record<string, never>)}>
      {children}
    </a>
  ),
}));
vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  onNetworkError: () => () => {},
  get: async (path: string) => {
    H.gets.push(path);
    const r = H.responses[path];
    if (r instanceof Error) throw r;
    return r === undefined ? {} : r;
  },
  post: async () => ({}),
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t-new", title: "t", updated_at: "2026-10-09T10:00:00" };
  },
  patch: async () => ({}),
  del: async () => ({}),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
vi.mock("@/lib/useReviews", () => ({ useReviews: () => ({ reviews: {}, reload: () => {} }) }));
vi.mock("@/components/kanban/KanbanBoard", () => ({
  KanbanBoard: () => <div data-testid="kanban-stub" />,
}));
vi.mock("@/lib/useChatStream", () => ({
  StreamError: H.FakeStreamError,
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  useChatStream: () => ({
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    run: async () => ({ reply: "ok", tools_used: [] }),
    abort: () => {},
  }),
}));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: false, reason: null, engine: null, listening: false, processing: false,
    transcript: "", interim: "", error: null, start: () => {}, stop: () => {}, reset: () => {},
  }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    supported: false, enabled: false, speaking: false, enable: () => {}, disable: () => {},
    toggle: () => {}, speak: () => {}, resetStream: () => {}, speakMore: () => {}, cancel: () => {},
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({
    byProvider: {}, signedOutByProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {},
  }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";
import ThreadGroups from "@/components/chat/ThreadGroups";
import ThreadRailFilter from "@/components/chat/ThreadRailFilter";
import { TurnReceipt, routeWarning } from "@/components/chat/TurnReceipt";
import { JumpToLatest } from "@/components/chat/JumpToLatest";
import { ProjectSurface, SURFACE_COLUMN } from "@/components/project/ProjectSurfaces";
import { __resetApiCache } from "@/lib/apiCache";
import { __resetChatList } from "@/lib/chatList";
import { createComposerStore } from "@/lib/composerStore";
import { sameTitleLabels, sameTitleTooltip } from "@/lib/sameTitleRows";
import { claimChatSlot, setChatSlot } from "@/lib/sidebarSlot";
import { LAST_VIEWED_KEY } from "@/lib/threadStatus";

/* ------------------------------------------------------------------ data */

const NOW = Date.now();
const ago = (ms: number) => new Date(NOW - ms).toISOString();
const MIN = 60_000;

const PROJECTS = [{ id: "p1", name: "Harbor Street Cafe" }];
const A1 = { id: "a1", title: "Q3 revenue by location", project_id: "p1", updated_at: ago(5 * MIN), messages: 2 };
const N1 = { id: "n1", title: "Lease options", updated_at: ago(20 * MIN), messages: 2 };

function classes(el: Element | null | undefined): string[] {
  return ((el?.getAttribute("class") ?? "") as string).split(/\s+/).filter(Boolean);
}

async function settle(): Promise<void> {
  for (let i = 0; i < 3; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

beforeEach(() => {
  __resetApiCache();
  __resetChatList();
  setChatSlot(null);
  H.nav.pathname = "/chat";
  H.gets.length = 0;
  H.responses = {
    "/chat/threads": { threads: [A1, N1] },
    "/chat/threads?project_id=p1": { threads: [A1] },
    "/projects": { projects: PROJECTS },
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/settings": { settings: {} },
    "/chat/approvals/pending": { approvals: [] },
  };
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.localStorage.setItem(LAST_VIEWED_KEY, JSON.stringify({ v: 1, since: NOW + 60_000, seen: {} }));
  window.history.replaceState({}, "", "/chat");
  Element.prototype.scrollIntoView = vi.fn();
  window.matchMedia = ((q: string) => ({
    matches: q.includes("min-width"),
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    onchange: null,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
});
afterEach(() => {
  cleanup();
  setChatSlot(null);
});

/* ======================================= (1) one quiet top to the list */

describe("the chat list in a project chat starts like it does off /chat", () => {
  async function railInSidebar() {
    const slot = document.createElement("div");
    slot.id = "ij-sidebar-chat-slot";
    document.body.appendChild(slot);
    claimChatSlot(slot);
    window.localStorage.setItem("ij_chat_project", "p1");
    render(<ChatPage />);
    const rail = await screen.findByTestId("chat-thread-rail");
    await within(rail).findByTitle("Q3 revenue by location");
    await waitFor(() => expect(within(rail).getByTestId("thread-rail-filter")).toBeTruthy());
    return { slot, rail };
  }

  it("in the sidebar: the search first, the filter just below it, and no extra heading or link", async () => {
    const { slot, rail } = await railInSidebar();
    try {
      expect(slot.contains(rail)).toBe(true);
      const search = within(rail).getByLabelText("Search chats");
      const filter = within(rail).getByTestId("thread-rail-filter");
      // Order: the search, then the filter, then the list.
      expect(search.compareDocumentPosition(filter) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(
        filter.compareDocumentPosition(within(rail).getByTestId("thread-groups")) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
      // Nothing in the list's top is an uppercase label (the sidebar's own
      // "Chats" heading names it), and there is no second "New chat" here.
      const top = Array.from(rail.querySelectorAll("*")).filter(
        (el) => !el.closest('[data-testid="thread-groups"]'),
      );
      expect(top.filter((el) => classes(el).includes("uppercase"))).toEqual([]);
      expect(within(rail).queryByText("Threads", { exact: true })).toBeNull();
      expect(within(rail).queryByRole("button", { name: /New chat/ })).toBeNull();
      expect(within(rail).queryByText(/Threads in/)).toBeNull();
    } finally {
      setChatSlot(null);
      slot.remove();
    }
  });

  it("the filter is two sentence-case ghosts; the one in effect is filled; pressing narrows the LIST only", async () => {
    const { slot, rail } = await railInSidebar();
    try {
      const all = within(rail).getByRole("button", { name: "All chats" });
      const only = within(rail).getByRole("button", { name: "Only Harbor Street Cafe" });
      for (const b of [all, only]) {
        expect(classes(b)).not.toContain("underline");
        expect(classes(b)).not.toContain("hover:underline");
        expect(classes(b)).not.toContain("uppercase");
      }
      expect(all.getAttribute("aria-pressed")).toBe("true");
      expect(only.getAttribute("aria-pressed")).toBe("false");
      expect(classes(all)).toContain("bg-white/[0.07]");
      expect(classes(only)).not.toContain("bg-white/[0.07]");
      await waitFor(() => expect(within(rail).getByTestId("thread-group-none")).toBeTruthy());
      fireEvent.click(only);
      await waitFor(() => expect(within(rail).queryByTestId("thread-group-none")).toBeNull());
      expect(only.getAttribute("aria-pressed")).toBe("true");
      expect(classes(only)).toContain("bg-white/[0.07]");
      fireEvent.click(all);
      expect(await within(rail).findByTestId("thread-group-none")).toBeTruthy();
    } finally {
      setChatSlot(null);
      slot.remove();
    }
  });

  it("Escape in the list's search clears it and goes no further; an empty box lets it through", async () => {
    const { slot, rail } = await railInSidebar();
    try {
      // The phone drawer closes on a window keydown (components/Sidebar).
      const outer = vi.fn();
      window.addEventListener("keydown", outer);
      const box = within(rail).getByLabelText("Search chats") as HTMLInputElement;
      fireEvent.change(box, { target: { value: "lease" } });
      fireEvent.keyDown(box, { key: "Escape" });
      expect(box.value).toBe("");
      expect(outer).not.toHaveBeenCalled();
      fireEvent.keyDown(box, { key: "Escape" });
      expect(outer).toHaveBeenCalledTimes(1);
      window.removeEventListener("keydown", outer);
    } finally {
      setChatSlot(null);
      slot.remove();
    }
  });

  it("CONTROL: with no project there is no filter", async () => {
    const slot = document.createElement("div");
    slot.id = "ij-sidebar-chat-slot";
    document.body.appendChild(slot);
    claimChatSlot(slot);
    try {
      render(<ChatPage />);
      const rail = await screen.findByTestId("chat-thread-rail");
      await within(rail).findByTitle("Lease options");
      await settle();
      expect(within(rail).queryByTestId("thread-rail-filter")).toBeNull();
      expect(within(rail).getByLabelText("Search chats")).toBeTruthy();
    } finally {
      setChatSlot(null);
      slot.remove();
    }
  });

  it("ThreadRailFilter on its own: names the project and reports the choice", () => {
    const onChange = vi.fn();
    render(<ThreadRailFilter projectName="Pier 9" only={false} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Only Pier 9" }));
    expect(onChange).toHaveBeenLastCalledWith(true);
    fireEvent.click(screen.getByRole("button", { name: "All chats" }));
    expect(onChange).toHaveBeenLastCalledWith(false);
    expect(screen.getByRole("group", { name: "Which chats to show" })).toBeTruthy();
  });
});

/* ================================== (2) same-titled rows: one second part */

describe("a same-titled row shows its time in place of its age", () => {
  const T1 = { id: "x1", title: "Check what changed", updated_at: new Date(2026, 9, 9, 18, 11, 46).toISOString() };
  const T2 = { id: "x2", title: "Check what changed", updated_at: new Date(2026, 9, 9, 20, 48, 5).toISOString() };
  const U = { id: "u1", title: "Lease options", updated_at: new Date(2026, 9, 9, 21, 30).toISOString() };
  const CLOCK = new Date(2026, 9, 9, 22, 0).getTime();

  it("twin rows carry ONE time (the short label), a unique row keeps its age", () => {
    render(<ThreadGroups threads={[T1, T2, U]} projects={[]} onOpen={() => {}} now={CLOCK} rowAction={() => <span />} />);
    for (const [id, label, tip] of [
      ["x1", "6:11 PM", "Today · 6:11:46 PM"],
      ["x2", "8:48 PM", "Today · 8:48:05 PM"],
    ] as const) {
      const row = screen.getByTestId(`thread-row-${id}`);
      const times = row.querySelectorAll("time");
      expect(times).toHaveLength(1);
      expect(times[0].textContent).toBe(label);
      expect(times[0].getAttribute("data-testid")).toBe(`thread-twin-${id}`);
      expect(times[0].getAttribute("title")).toBe(tip);
      // No age next to it (the old "6:11:46 PM 5h").
      expect(row.textContent).not.toMatch(/\d+[mhd]$/);
      // While the row has an action, the time steps aside for the ⋯ on hover.
      expect(classes(times[0])).toContain("[@media(hover:hover)]:group-hover/thread:opacity-0");
    }
    const unique = screen.getByTestId("thread-row-u1");
    expect(unique.querySelectorAll("time")).toHaveLength(1);
    expect(unique.querySelector("time")!.textContent).toBe("30m");
    expect(screen.queryByTestId("thread-twin-u1")).toBeNull();
  });

  it("the label never carries seconds; the tooltip does", () => {
    const sameMinute = sameTitleLabels(
      [
        { id: "a", title: "Same", updated_at: new Date(2026, 9, 9, 18, 11, 5).toISOString() },
        { id: "b", title: "Same", updated_at: new Date(2026, 9, 9, 18, 11, 42).toISOString() },
      ],
      CLOCK,
    );
    expect([...sameMinute.values()]).toEqual(["6:11 PM", "6:11 PM"]);
    expect(sameTitleTooltip(new Date(2026, 9, 9, 18, 11, 42).toISOString(), CLOCK)).toBe("Today · 6:11:42 PM");
    expect(sameTitleTooltip(new Date(2026, 9, 6, 9, 0, 7).toISOString(), CLOCK)).toBe("Oct 6 · 9:00:07 AM");
    expect(sameTitleTooltip(null, CLOCK)).toBe("");
  });

  // Fix round: the first cut was right only for chats made TODAY. From the
  // next day on the twins read "Yesterday · 8:48 PM" and the title clipped
  // to about 10 characters again. Every twin here is from yesterday.
  it("twins from yesterday carry ONE short part (no '·'); the day is in the tooltip", () => {
    const Y1 = { id: "y1", title: "Check what changed", updated_at: new Date(2026, 9, 8, 18, 11, 46).toISOString() };
    const Y2 = { id: "y2", title: "Check what changed", updated_at: new Date(2026, 9, 8, 20, 48, 5).toISOString() };
    const Y3 = { id: "y3", title: "Check what changed", updated_at: new Date(2026, 9, 8, 9, 2, 0).toISOString() };
    render(<ThreadGroups threads={[Y2, Y1, Y3]} projects={[]} onOpen={() => {}} now={CLOCK} rowAction={() => <span />} />);
    for (const [id, label, tip] of [
      ["y1", "6:11 PM", "Yesterday · 6:11:46 PM"],
      ["y2", "8:48 PM", "Yesterday · 8:48:05 PM"],
      ["y3", "9:02 AM", "Yesterday · 9:02:00 AM"],
    ] as const) {
      const twin = screen.getByTestId(`thread-twin-${id}`);
      expect(twin.textContent).toBe(label);
      expect(twin.textContent).not.toContain("·");
      expect(twin.getAttribute("title")).toBe(tip);
      expect(screen.getByTestId(`thread-row-${id}`).querySelectorAll("time")).toHaveLength(1);
    }
    // The pure rule agrees, and twins on different days say only the day.
    const apart = sameTitleLabels(
      [
        { id: "a", title: "Same", updated_at: new Date(2026, 9, 8, 20, 48).toISOString() },
        { id: "b", title: "Same", updated_at: new Date(2026, 9, 6, 9, 0).toISOString() },
      ],
      CLOCK,
    );
    expect(apart.get("a")).toBe("Yesterday");
    expect(apart.get("b")).toBe("Oct 6");
    for (const v of apart.values()) expect(v).not.toContain("·");
  });
});

/* ============================== (3) one way to name who answered */

describe("a substitute is named by its model, like a normal row", () => {
  const FAILOVER = {
    requested: "fleet-local",
    provider: "anthropic",
    model: "claude-sonnet-4-6",
    reason: "failover",
    from: "fleet-local",
  };

  it("the failover chip says the model the caller names; the provider is in its title and the detail", () => {
    render(<TurnReceipt inline modelName="Sonnet 4.6 (catalog)" route={FAILOVER} />);
    const chip = screen.getByTestId("turn-route-warning");
    expect(chip.textContent).toBe("answered by Sonnet 4.6 (catalog) (fleet-local was not available)");
    expect(chip.getAttribute("title")).toBe(
      "Served by Anthropic. anthropic · claude-sonnet-4-6 (failover from fleet-local)",
    );
    expect(classes(chip)).toContain("text-tone-warn");
    fireEvent.click(screen.getByTestId("turn-receipt"));
    const detail = screen.getByTestId("turn-receipt-detail");
    expect(detail.textContent).toContain("Anthropic");
    expect(detail.textContent).toContain("claude-sonnet-4-6");
  });

  it("with no name given, the name the route's model spells; with no model, the provider", () => {
    expect(routeWarning(FAILOVER)).toBe("answered by Sonnet 4.6 (fleet-local was not available)");
    expect(routeWarning({ requested: "ollama", provider: "anthropic", model: "claude-opus-4-8", reason: "explicit" })).toBe(
      "answered by Opus 4.8 (you asked for Ollama)",
    );
    expect(routeWarning({ ...FAILOVER, model: "" })).toBe("answered by Anthropic (fleet-local was not available)");
    // The mock wording is unchanged.
    expect(routeWarning({ provider: "mock", model: "mock", reason: "mock" }, "Opus 4.8")).toBe(
      "Mock answer. No real model ran.",
    );
  });

  it("the normal row and the failover row use the same name for the same model", () => {
    render(
      <>
        <TurnReceipt inline modelName="Opus 4.8" route={{ requested: "", provider: "anthropic", model: "claude-opus-4-8", reason: "default" }} />
        <TurnReceipt inline modelName="Opus 4.8" route={{ ...FAILOVER, model: "claude-opus-4-8" }} />
      </>,
    );
    expect(screen.getByTestId("turn-answered-by").textContent).toBe("answered by Opus 4.8");
    expect(screen.getByTestId("turn-route-warning").textContent).toMatch(/^answered by Opus 4\.8 \(/);
  });
});

/* ============================== (4) the receipt's file is a plain row */

describe("the expanded receipt lists its file as a plain row", () => {
  it("normal font, no chip box; pressing it still opens the full path", () => {
    const open = vi.fn();
    const full = "C:\\Work\\Harbor\\summary.md";
    render(
      <TurnReceipt
        inline
        modelName="Opus 4.8"
        route={{ requested: "", provider: "anthropic", model: "claude-opus-4-8", reason: "default" }}
        documents={[full]}
        onOpenDocument={open}
      />,
    );
    fireEvent.click(screen.getByTestId("turn-receipt"));
    const doc = screen.getByTestId("turn-doc");
    expect(doc.textContent).toBe("summary.md");
    expect(doc.getAttribute("title")).toBe(full);
    const cls = classes(doc);
    expect(cls).not.toContain("font-mono");
    expect(cls.filter((c) => c.startsWith("bg-"))).toEqual([]);
    expect(cls.filter((c) => /^border/.test(c))).toEqual([]);
    expect(cls).toContain("text-[12px]");
    fireEvent.click(doc);
    expect(open).toHaveBeenCalledWith(full);
    expect(screen.getByTestId("turn-receipt-detail").querySelector(".font-mono")).toBeNull();
  });
});

/* ===================================== (5) a calm Jump to latest */

describe("Jump to latest is a calm ghost chip", () => {
  it("a hairline and an ink fill that fills on hover; no accent border, no glow", () => {
    render(<JumpToLatest store={createComposerStore()} busy={false} show menuOpen={false} onJump={() => {}} />);
    const pill = screen.getByTestId("jump-to-latest");
    const cls = classes(pill);
    expect(cls.filter((c) => c.includes("accent") && !c.startsWith("focus-visible:"))).toEqual([]);
    expect(cls.filter((c) => c.includes("glow") || c.startsWith("shadow"))).toEqual([]);
    expect(cls).toEqual(
      expect.arrayContaining(["border", "border-white/[0.1]", "bg-ink-850/95", "hover:bg-ink-800", "text-zinc-300"]),
    );
    expect(pill.textContent?.trim()).toBe("Jump to latest");
  });
});

/* ===================== (6) Tasks and Media in the chat's reading column */

describe("project views keep the chat's reading line", () => {
  it("Tasks and Media sit in the chat column; the Board keeps its width", async () => {
    const column = SURFACE_COLUMN.split(" ");
    expect(column).toEqual(["mx-auto", "w-full", "max-w-[760px]"]);
    const tasks = render(<ProjectSurface projectId="p1" hasRoot view="tasks" />);
    await screen.findByTestId("project-tasks");
    expect(classes(screen.getByTestId("project-surface"))).toEqual(expect.arrayContaining(column));
    tasks.unmount();
    H.responses["/creative/items?project_id=p1&limit=200"] = { items: [] };
    const media = render(<ProjectSurface projectId="p1" hasRoot view="media" />);
    await screen.findByText(/No media in this project yet/);
    expect(classes(screen.getByTestId("project-surface"))).toEqual(expect.arrayContaining(column));
    media.unmount();
    H.responses["/sessions?project_id=p1"] = {
      sessions: [{ id: "s1", project_id: "p1", status: "completed", task: "t", created_at: "x" }],
    };
    render(<ProjectSurface projectId="p1" hasRoot view="board" />);
    await screen.findByTestId("kanban-stub");
    expect(classes(screen.getByTestId("project-surface"))).not.toContain("max-w-[760px]");
  });

  it("an empty board's line reads in the chat column too", async () => {
    H.responses["/sessions?project_id=p1"] = { sessions: [] };
    render(<ProjectSurface projectId="p1" hasRoot view="board" />);
    const line = await screen.findByText("No sessions in this project yet. Run a task from the Tasks tab.");
    const state = line.closest('[data-testid="project-board-state"]');
    expect(classes(state)).toEqual(expect.arrayContaining(["mx-auto", "max-w-[760px]"]));
  });
});

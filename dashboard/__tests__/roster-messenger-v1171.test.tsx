import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

/**
 * Roster messenger + portraits (v1.171.0, P2).
 *
 * WHAT THESE TESTS GUARD:
 *  - the roster's messenger preview shows REAL joined activity (last_message
 *    + relative last_active) and falls back to the static description when
 *    the daemon reports none — never both, never invented;
 *  - faces carry TRUTH: the roster face is always mood "idle" (the roster
 *    has no live busy signal), and a stored portrait always wins;
 *  - the Setup card's portrait row posts the exact avatar bodies (a dropped
 *    `generate` flag or a mangled path fails, not "still posts something"),
 *    and the daemon's honest no-image-model 409 text is shown verbatim.
 */

const hooks = vi.hoisted(() => ({
  api: {} as Record<string, unknown>,
  posts: [] as Array<{ path: string; body: Record<string, unknown> }>,
  deletes: [] as string[],
  postResult: {} as unknown,
  postFail: null as string | null,
}));

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (hooks.api[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? (hooks.api[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError,
    API_BASE: "http://test",
    ijToken: () => "tok-1",
    get: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: (path: string) => {
      hooks.deletes.push(path);
      return Promise.resolve({});
    },
    post: (path: string, body: Record<string, unknown>) => {
      hooks.posts.push({ path, body });
      return hooks.postFail
        ? Promise.reject(new ApiError(hooks.postFail, 409))
        : Promise.resolve(hooks.postResult);
    },
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
    "whileHover",
  ]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  return {
    AnimatePresence: ({ children }: { children?: unknown }) =>
      createElement(Fragment, null, children as never),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => tagFor(String(tag)),
    }),
  };
});

import { faceShape } from "@/components/agents/AgentFace";
import { RosterStrip, type RosterEntry } from "@/components/agents/RosterStrip";
import { SetupCard, type DynamicAgentFull } from "@/components/agents/SetupCard";

import { SetupCardHarness } from "./helpers/setupCardHarness";
const ROSTER: RosterEntry[] = [
  {
    name: "builder",
    kind: "builtin",
    description: "hands-on doer",
    delegable: true,
    healthy: true,
    stats: null,
    last_active: "2026-08-12T10:00:00+00:00",
    last_message: "Done. Wrote RESULT.md summarizing the task.",
    avatar: null,
  },
  {
    name: "custom:analyst",
    kind: "dynamic",
    description: "your analyst",
    delegable: true,
    healthy: true,
    stats: null,
    last_active: null,
    last_message: null,
    avatar: null,
  },
  {
    name: "remote:opus-box",
    kind: "remote",
    description: "remote agent (http-task)",
    delegable: true,
    healthy: true,
    stats: null,
    last_active: "2026-08-11T09:00:00+00:00",
    last_message: "Reviewed the draft — two issues flagged.",
    avatar: "/agents/opus-box/avatar",
  },
];

const pick = () => screen.getByLabelText("Choose an agent") as HTMLSelectElement;

beforeEach(() => {
  hooks.api = { "/agents/roster": { roster: ROSTER } };
  hooks.posts = [];
  hooks.deletes = [];
  hooks.postResult = {};
  hooks.postFail = null;
  // The Setup card starts open so its rows are reachable without a toggle.
  window.localStorage.setItem("ij_agents_setup_open", "1");
});
afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

/* ------------------------------------------------ RosterStrip: messenger --- */

describe("RosterStrip — messenger preview", () => {
  it("shows the daemon's last_message with the relative time, not the description", () => {
    render(<RosterStrip />);
    const preview = screen.getByTestId("roster-preview");
    expect(preview.textContent).toContain(
      "Done. Wrote RESULT.md summarizing the task.",
    );
    // Relative time renders from last_active ("…ago"), never a raw ISO dump.
    expect(screen.getByTestId("roster-when").textContent).toMatch(/ago|now/);
    // The static description yields to the more current truth.
    expect(screen.queryByText("hands-on doer")).toBeNull();
  });

  it("falls back to the description exactly as before when there is no activity", () => {
    render(<RosterStrip />);
    fireEvent.change(pick(), { target: { value: "custom:analyst" } });
    expect(screen.queryByTestId("roster-preview")).toBeNull();
    expect(screen.queryByTestId("roster-when")).toBeNull();
    expect(screen.getByText("your analyst")).toBeTruthy();
  });

  it("renders no preview and no crash when the daemon predates the fields", () => {
    hooks.api["/agents/roster"] = {
      roster: [
        {
          name: "builder",
          kind: "builtin",
          description: "hands-on doer",
          delegable: true,
          healthy: true,
          stats: null,
        },
      ],
    };
    render(<RosterStrip />);
    expect(screen.queryByTestId("roster-preview")).toBeNull();
    expect(screen.getByText("hands-on doer")).toBeTruthy();
  });
});

describe("RosterStrip — faces carry truth", () => {
  it("draws the deterministic idle face — never an invented busy mood", () => {
    render(<RosterStrip />);
    const face = screen.getByTestId("agent-face");
    expect(face.getAttribute("data-face-mood")).toBe("idle");
    // Same seed as every other surface: the shape IS faceShape(name).
    expect(face.getAttribute("data-face-shape")).toBe(faceShape("builder"));
  });

  it("a stored portrait wins over the drawn face, token riding the query", () => {
    render(<RosterStrip />);
    fireEvent.change(pick(), { target: { value: "remote:opus-box" } });
    expect(screen.queryByTestId("agent-face")).toBeNull();
    const img = screen.getByAltText("opus-box") as HTMLImageElement;
    // The cache-buster is the row's last_active (low-resolution rev): a
    // replaced portrait bumps last_active on the next roster fetch, so the
    // browser can't keep serving the stale cached image.
    expect(img.src).toBe(
      "http://test/agents/opus-box/avatar" +
        `?v=${encodeURIComponent("2026-08-11T09:00:00+00:00")}&token=tok-1`,
    );
  });

  it("the avatar cache key tracks last_active — a bumped row yields a new URL", () => {
    const bumped = ROSTER.map((e) =>
      e.name === "remote:opus-box"
        ? { ...e, last_active: "2026-08-12T12:00:00+00:00" }
        : e,
    );
    hooks.api["/agents/roster"] = { roster: bumped };
    render(<RosterStrip />);
    fireEvent.change(pick(), { target: { value: "remote:opus-box" } });
    const img = screen.getByAltText("opus-box") as HTMLImageElement;
    expect(img.src).toContain(
      `?v=${encodeURIComponent("2026-08-12T12:00:00+00:00")}`,
    );
    // A mutation hardcoding v=0 (or dropping the param) fails here AND above.
    expect(img.src).not.toContain("?v=0");
  });

  it("keeps Talk/Give-work wiring intact after the face refactor", () => {
    const onAssign = vi.fn();
    const onTalk = vi.fn();
    render(<RosterStrip onAssign={onAssign} onTalk={onTalk} />);
    fireEvent.change(pick(), { target: { value: "custom:analyst" } });
    fireEvent.click(screen.getByRole("button", { name: /Give work/ }));
    fireEvent.click(screen.getByRole("button", { name: /Talk/ }));
    expect(onAssign).toHaveBeenCalledWith("dynamic", "analyst");
    expect(onTalk).toHaveBeenCalledWith("dynamic", "analyst");
  });
});

/* ------------------------------------------------- SetupCard: portraits --- */

function dynamicAgent(over: Partial<DynamicAgentFull> = {}): DynamicAgentFull {
  return {
    name: "analyst",
    description: "test helper",
    provider: "",
    model: "",
    system_prompt: "You are analytical.",
    tools: [],
    avatar: null,
    ...over,
  };
}

function renderSetup(agent = dynamicAgent(), onAgentsChanged = vi.fn()) {
  render(
    <SetupCardHarness
      // These cases reach straight for a row without pressing the header, so
      // the card must start open. It used to get that from the seeded
      // `ij_agents_setup_open` in beforeEach; since v1.185.0 the disclosure is
      // a prop the page owns, so the harness states it outright.
      initialOpen
      builtin={["builder"]}
      dynamic={[agent]}
      remotes={[]}
      models={[]}
      onAgentsChanged={onAgentsChanged}
      onRemotesChanged={vi.fn()}
    />,
  );
  return onAgentsChanged;
}

function openAvatarRow(name = "analyst") {
  fireEvent.click(screen.getByTitle(`Edit the persona of "${name}"`));
  return screen.getByTestId(`avatar-row-${name}`);
}

describe("SetupCard — the portrait row", () => {
  it("edit mode shows Upload + Generate; Remove only exists once a portrait does", () => {
    renderSetup();
    const row = openAvatarRow();
    expect(within(row).getByText("Upload")).toBeTruthy();
    expect(within(row).getByRole("button", { name: /Generate/ })).toBeTruthy();
    expect(within(row).queryByRole("button", { name: /Remove/ })).toBeNull();
  });

  it("Generate posts the exact body to the agent's avatar route", async () => {
    const onChanged = renderSetup();
    const row = openAvatarRow();
    fireEvent.click(within(row).getByRole("button", { name: /Generate/ }));
    await waitFor(() => expect(hooks.posts.length).toBe(1));
    expect(hooks.posts).toEqual([
      { path: "/agents/analyst/avatar", body: { generate: true } },
    ]);
    // Whether a portrait now EXISTS is daemon truth — the card refetches.
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("shows the daemon's honest no-image-model 409 verbatim — no placeholder", async () => {
    hooks.postFail =
      "no image model is connected — add a 'pixio' secret (Secrets page) or set PIXIO_API_KEY to enable portrait generation";
    const onChanged = renderSetup();
    const row = openAvatarRow();
    fireEvent.click(within(row).getByRole("button", { name: /Generate/ }));
    await waitFor(() =>
      expect(screen.getByText(/no image model is connected/)).toBeTruthy(),
    );
    expect(screen.getByText(/PIXIO_API_KEY/)).toBeTruthy();
    expect(onChanged).not.toHaveBeenCalled();
    // The row's face stays the DRAWN one — nothing pretends a portrait landed.
    expect(within(row).getByTestId("agent-face")).toBeTruthy();
  });

  it("Upload reads the file and posts its bare base64", async () => {
    renderSetup();
    openAvatarRow();
    const input = screen.getByLabelText(
      "Upload a portrait for analyst",
    ) as HTMLInputElement;
    const bytes = new Uint8Array([0x89, 0x50, 0x4e, 0x47]);
    const file = new File([bytes], "face.png", { type: "image/png" });
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(hooks.posts.length).toBe(1));
    expect(hooks.posts).toEqual([
      { path: "/agents/analyst/avatar", body: { image_b64: "iVBORw==" } },
    ]);
  });

  it("an oversized pick fails client-side with a plain line and never posts", async () => {
    renderSetup();
    openAvatarRow();
    const input = screen.getByLabelText(
      "Upload a portrait for analyst",
    ) as HTMLInputElement;
    const big = new File([new Uint8Array(2 * 1024 * 1024 + 1)], "huge.png", {
      type: "image/png",
    });
    fireEvent.change(input, { target: { files: [big] } });
    await waitFor(() =>
      expect(screen.getByText("portrait too large — 2 MB max")).toBeTruthy(),
    );
    expect(hooks.posts).toHaveLength(0);
  });

  it("with a stored portrait: the row face is the portrait and Remove DELETEs it", async () => {
    const onChanged = renderSetup(
      dynamicAgent({ avatar: "/agents/analyst/avatar" }),
    );
    const row = openAvatarRow();
    // Portrait wins inside the row preview (cache-busted, token-carrying).
    const img = within(row).getByAltText("analyst") as HTMLImageElement;
    expect(img.src).toContain("http://test/agents/analyst/avatar?v=");
    expect(img.src).toContain("token=tok-1");
    fireEvent.click(within(row).getByRole("button", { name: /Remove/ }));
    await waitFor(() =>
      expect(hooks.deletes).toEqual(["/agents/analyst/avatar"]),
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });
});

describe("SetupCard — the create form previews the face live", () => {
  it("typing a name draws that name's deterministic face", () => {
    renderSetup();
    const nameBox = screen.getByLabelText("Agent name") as HTMLInputElement;
    fireEvent.change(nameBox, { target: { value: "skeptic" } });
    const form = nameBox.closest("form") as HTMLElement;
    const face = within(form).getByTestId("agent-face");
    expect(face.getAttribute("data-face-shape")).toBe(faceShape("skeptic"));
    // A different name draws a different (still deterministic) face seed.
    fireEvent.change(nameBox, { target: { value: "archivist" } });
    expect(
      within(form).getByTestId("agent-face").getAttribute("data-face-shape"),
    ).toBe(faceShape("archivist"));
  });
});

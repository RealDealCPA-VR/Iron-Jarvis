/**
 * The tools picker, and the truth about what an agent HOLDS (v1.178.0).
 *
 * MEASURED BUG: `SetupCard.tsx` POSTed `tools: []` hardcoded and had no tools
 * control at all, so every agent created from this page stored a literal empty
 * allowlist and advertised NOTHING to its model. The daemon now reads an empty
 * stored list as "not specified" and inherits the base type's roster, and
 * returns two fields — `tools` (stored) and `effective_tools` (resolved).
 *
 * WHAT THESE TESTS GUARD is the asymmetry between them. Render `effective_tools`
 * (the stored list alone says "no tools" about an agent that works) but PATCH
 * `tools` — sending the effective roster back would look identical on screen
 * and silently freeze inheritance into an explicit allowlist the first time the
 * user saved an unrelated edit.
 */

import { describe, expect, it, vi, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const calls: { path: string; body: unknown }[] = [];
/** GET /tools — the real registry endpoint the picker reads its options from.
 *  Overridable per test so the fetch-failure degrade can be driven. */
let toolsResponse: () => Promise<unknown> = async () => ({
  tools: [
    { name: "read_file", description: "read a file" },
    { name: "write_file", description: "write a file" },
    { name: "shell", description: "run a command" },
    { name: "web_search", description: "search the web" },
  ],
});

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status = 500;
  },
  del: vi.fn(async () => ({})),
  post: vi.fn(async () => ({})),
  patch: vi.fn(async (path: string, body: unknown) => {
    calls.push({ path, body });
    return {};
  }),
  get: vi.fn(async (path: string) => {
    if (path === "/tools") return toolsResponse();
    return {};
  }),
  API_BASE: "",
  ijToken: () => "",
}));

import { SetupCard } from "@/components/agents/SetupCard";
import type { DynamicAgentFull } from "@/components/agents/SetupCard";

/** The default case this feature exists for: nothing stored, so the daemon
 *  resolved the base type's roster into `effective_tools`. */
const INHERITING: DynamicAgentFull = {
  name: "skeptic",
  description: "challenges assumptions",
  base_type: "builder",
  system_prompt: "You are a skeptic.",
  tools: [],
  effective_tools: ["read_file", "write_file", "shell"],
};

function renderCard(agent: DynamicAgentFull) {
  return render(
    <SetupCard
      builtin={[]}
      dynamic={[agent]}
      remotes={[]}
      models={[]}
      onAgentsChanged={() => {}}
      onRemotesChanged={() => {}}
    />,
  );
}

/** IDEMPOTENT expand — the card persists its open state in localStorage, so an
 *  unconditional click would CLOSE it on any test after the first. */
function expand() {
  const toggle = screen.getByRole("button", { name: /set up agents/i });
  if (toggle.getAttribute("aria-expanded") !== "true") fireEvent.click(toggle);
}

async function openEditor(name = "skeptic") {
  expand();
  const edit = await screen.findByTitle(new RegExp(`Edit the persona of "${name}"`, "i"));
  fireEvent.click(edit);
  return within(screen.getByTestId(`tools-editor-${name}`));
}

const summary = (name = "skeptic") => within(screen.getByTestId(`tools-summary-${name}`));
const lastBody = () => calls[calls.length - 1].body as Record<string, unknown>;

afterEach(() => {
  cleanup();
  calls.length = 0;
  localStorage.clear();
  toolsResponse = async () => ({
    tools: [
      { name: "read_file", description: "read a file" },
      { name: "write_file", description: "write a file" },
      { name: "shell", description: "run a command" },
      { name: "web_search", description: "search the web" },
    ],
  });
});

describe("what the card SAYS an agent holds", () => {
  it("shows the INHERITED tools of an agent with an empty stored list", () => {
    // The whole bug in one assertion: `tools` is [] here, and a card reading
    // only that field would render an empty roster for a working agent.
    renderCard(INHERITING);
    expand();
    for (const t of ["read_file", "write_file", "shell"]) {
      expect(summary().getByText(t)).toBeTruthy();
    }
  });

  it("says the roster is inherited, and from what", () => {
    renderCard(INHERITING);
    expand();
    expect(summary().getByText(/inherited from the builder base type/i)).toBeTruthy();
  });

  it("calls an explicit allowlist chosen, not inherited", () => {
    renderCard({ ...INHERITING, tools: ["read_file"], effective_tools: ["read_file"] });
    expand();
    expect(summary().getByText(/chosen/i)).toBeTruthy();
    expect(summary().queryByText(/inherited/i)).toBeNull();
    // ...and the chips are still there. The label alone carries a COUNT; the
    // row is also where the user checks WHICH tools, and a chip list that
    // silently rendered only for inherited agents would leave an explicitly
    // narrowed agent looking unexamined. (Reviewer addition: dropping the chip
    // row for `origin === "explicit"` left the whole suite green.)
    expect(summary().getByText("read_file")).toBeTruthy();
  });

  it("still names the base type generically when the daemon omits base_type", () => {
    const { base_type: _drop, ...noBase } = INHERITING;
    renderCard(noBase as DynamicAgentFull);
    expand();
    expect(summary().getByText(/inherited from its base type/i)).toBeTruthy();
  });
});

describe("an older daemon that does not send effective_tools", () => {
  /** Same agent, minus the v1.178.0 field — nothing stored, nothing resolved. */
  const OLD: DynamicAgentFull = {
    name: "skeptic",
    description: "challenges assumptions",
    base_type: "builder",
    system_prompt: "You are a skeptic.",
    tools: [],
  };

  it("says the agent inherits rather than rendering an empty roster as fact", () => {
    renderCard(OLD);
    expand();
    expect(summary().getByText(/inherits the builder base type/i)).toBeTruthy();
    expect(summary().getByText(/doesn’t report which ones/i)).toBeTruthy();
  });

  it("claims no count and lists no tools it cannot know", () => {
    renderCard(OLD);
    expand();
    // Not "0 inherited" and not an empty chip row — both read as "can do
    // nothing", which is the lie this whole pair of fields exists to stop.
    expect(summary().queryByText(/\b0 inherited/i)).toBeNull();
    expect(summary().queryByText("read_file")).toBeNull();
  });

  it("still lets the user pin an explicit set", async () => {
    renderCard(OLD);
    const editor = await openEditor();
    fireEvent.click(editor.getByRole("button", { name: /choose specific tools/i }));
    await waitFor(() => expect(editor.getByLabelText("read_file")).toBeTruthy());
    fireEvent.click(editor.getByLabelText("read_file"));
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => expect(lastBody().tools).toEqual(["read_file"]));
  });
});

describe("choosing an explicit set", () => {
  it("PATCHes exactly the chosen tools", async () => {
    renderCard(INHERITING);
    const editor = await openEditor();
    // Starts from what the agent actually holds, so narrowing is unchecking.
    fireEvent.click(editor.getByRole("button", { name: /choose specific tools/i }));
    await waitFor(() => expect(editor.getByLabelText("shell")).toBeTruthy());
    fireEvent.click(editor.getByLabelText("shell")); // drop the dangerous one
    fireEvent.click(editor.getByLabelText("write_file")); // and the writer
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(calls[0].path).toBe("/agents/skeptic");
      expect(lastBody().tools).toEqual(["read_file"]);
    });
  });

  it("narrows an agent that ALREADY has an explicit list", async () => {
    // THE COMMON FLOW, and it was the one unguarded hole: every other test
    // reaches the checkboxes through "Choose specific tools" / "Use inherited
    // tools", and BOTH of those buttons set the dirty flag themselves. An agent
    // that already stores an allowlist opens straight into the picker, so the
    // ONLY thing that can mark the save dirty is the checkbox itself — and with
    // `setToolsDirty(true)` deleted from `toggleTool` the entire 14-test suite
    // stayed green while the user's unchecked tool was silently dropped from
    // the PATCH (Save succeeds, nothing changes, no error anywhere).
    renderCard({
      ...INHERITING,
      tools: ["read_file", "write_file", "shell"],
      effective_tools: ["read_file", "write_file", "shell"],
    });
    const editor = await openEditor();
    await waitFor(() => expect(editor.getByLabelText("shell")).toBeTruthy());
    fireEvent.click(editor.getByLabelText("shell"));
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(lastBody().tools).toEqual(["read_file", "write_file"]));
  });

  it("offers tools from the daemon's registry, not an invented list", async () => {
    renderCard(INHERITING);
    const editor = await openEditor();
    fireEvent.click(editor.getByRole("button", { name: /choose specific tools/i }));
    // `web_search` is in GET /tools but NOT in this agent's roster — it can
    // only be on screen because the catalog was fetched.
    await waitFor(() => expect(editor.getByLabelText("web_search")).toBeTruthy());
  });

  it("keeps a tool the agent holds even when the registry no longer lists it", async () => {
    // An MCP pack disconnects: `mcp__box__upload` vanishes from GET /tools but
    // the agent still holds it. If the picker dropped it, the next save would
    // silently strip it from the allowlist.
    renderCard({
      ...INHERITING,
      tools: ["read_file", "mcp__box__upload"],
      effective_tools: ["read_file", "mcp__box__upload"],
    });
    const editor = await openEditor();
    await waitFor(() => expect(editor.getByLabelText("mcp__box__upload")).toBeTruthy());
    expect((editor.getByLabelText("mcp__box__upload") as HTMLInputElement).checked).toBe(true);
  });

  it("degrades to the agent's own roster when the catalog cannot be loaded", async () => {
    toolsResponse = async () => {
      throw new Error("daemon offline");
    };
    renderCard(INHERITING);
    const editor = await openEditor();
    fireEvent.click(editor.getByRole("button", { name: /choose specific tools/i }));
    await waitFor(() => expect(editor.getByText(/couldn’t load the full tool list/i)).toBeTruthy());
    // Narrowing still works against what the agent actually holds.
    expect(editor.getByLabelText("read_file")).toBeTruthy();
  });
});

describe("clearing an explicit set", () => {
  it("PATCHes an EMPTY list, which is the daemon's 'not specified'", async () => {
    renderCard({ ...INHERITING, tools: ["read_file"], effective_tools: ["read_file"] });
    const editor = await openEditor();
    fireEvent.click(editor.getByRole("button", { name: /use inherited tools/i }));
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(lastBody().tools).toEqual([]));
  });
});

describe("a save that had nothing to do with tools", () => {
  it("does NOT convert inheritance into an allowlist", async () => {
    renderCard(INHERITING);
    const editor = await openEditor();
    // Never touch the picker. Change only the description.
    fireEvent.change(screen.getByLabelText(/description for skeptic/i), {
      target: { value: "asks the awkward questions" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      const body = lastBody();
      expect(body.description).toBe("asks the awkward questions");
      // The field is absent entirely — an untouched picker has no opinion.
      expect("tools" in body).toBe(false);
      // And under no circumstances the RESOLVED roster: that would look
      // identical on screen and pin the agent to today's tool list forever.
      expect(body.tools).not.toEqual(INHERITING.effective_tools);
    });
    expect(editor).toBeTruthy();
  });

  it("leaves an explicit allowlist alone too", async () => {
    renderCard({ ...INHERITING, tools: ["read_file"], effective_tools: ["read_file"] });
    await openEditor();
    fireEvent.change(screen.getByLabelText(/persona prompt for skeptic/i), {
      target: { value: "You are a very tired skeptic." },
    });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      const body = lastBody();
      expect(body.system_prompt).toBe("You are a very tired skeptic.");
      expect("tools" in body).toBe(false);
    });
  });
});

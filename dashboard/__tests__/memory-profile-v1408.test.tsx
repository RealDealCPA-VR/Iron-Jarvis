/**
 * v1.408.0 — follow-ups to v1.406.0's memory profiles (called "Brain
 * profile" then):
 *
 *   1. The name a person reads is "Memory profile" everywhere (Settings row,
 *      the section's empty option, the project page's select, the chat
 *      drawer's line); Settings search still finds it by "brain profile".
 *   2. An MCP memory base's "What each tool does" editor on its Long-term
 *      row: the saved choice wins over the proposal, a fixed value has a box,
 *      a required argument cannot be left empty, problems are said, the PUT
 *      body is exact, a 400 sentence keeps the form open, 404 draws nothing,
 *      and the row's badge says whether it was ever confirmed.
 *   3. Check a memory profile (steps with their tones) and Try a test save
 *      (two presses, its words, a 409 sentence; 404 hides both).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    gets: {} as Record<string, unknown>,
    getCalls: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    puts: [] as { path: string; body: unknown }[],
    answers: {} as Record<string, unknown>,
  };
});

async function answer(key: string, body?: unknown): Promise<unknown> {
  const r = H.answers[key];
  const v = typeof r === "function" ? (r as (b: unknown) => unknown)(body) : r;
  if (v instanceof Error) throw v;
  return v ?? {};
}

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    H.getCalls.push(path);
    const r = path in H.gets ? H.gets[path] : H.gets[path.split("?")[0]];
    const v = typeof r === "function" ? (r as () => unknown)() : r;
    if (v instanceof Error) throw v;
    if (v === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return v;
  },
  post: async (path: string, body?: unknown) => {
    H.posts.push({ path, body });
    return answer(`POST ${path}`, body);
  },
  put: async (path: string, body?: unknown) => {
    H.puts.push({ path, body });
    return answer(`PUT ${path}`, body);
  },
  patch: async () => ({}),
  del: async () => ({}),
}));
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ online: true, checking: false, health: { providers: [] }, refresh: vi.fn() }),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/settings",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(window.location.search),
  redirect: () => {
    throw new Error("NEXT_REDIRECT");
  },
}));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: { children?: ReactNode; href: string }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));
vi.mock("@/components/settings/pages/LegacySettingsForm", () => ({
  default: () => <div data-testid="legacy-form">legacy</div>,
  LocalCapabilitiesCard: () => <div>caps</div>,
  ContextWindowsCard: () => <div>windows</div>,
}));

import { BrainProfiles } from "@/components/settings/BrainProfiles";
import { SettingsHome } from "@/components/settings/SettingsHome";
import { ProjectBrainSelect } from "@/components/project/ProjectBrainSelect";
import { ChatBrainLine } from "@/components/chat/ChatBrainLine";
import { McpBaseRoles } from "@/components/memory/McpBaseRoles";
import { LongTerm } from "@/components/memory/LongTerm";
import { ROLES_INTRO, decodeRolesView, rolesBody, startingChoice } from "@/lib/mcpBaseRoles";
import { decodeCheck, decodeTestSave } from "@/lib/memoryProfileCheck";

const W = { timeout: 10_000 };

const NOTION = {
  id: "bp1",
  name: "Notion",
  primary: "notion",
  search_first: true,
  save_to: "primary",
  prefer_update: true,
  on_conflict: "ask",
  guide_ref: "",
  skills: [],
  repo_routing: false,
  created_at: "2026-10-29T10:00:00Z",
  updated_at: "2026-10-29T10:00:00Z",
};
const STATE = {
  profiles: [NOTION],
  selected: "bp1",
  bases: [{ name: "notion", label: "Notion", kind: "mcp", available: true }],
  pending: 0,
};

const TOOLS = [
  {
    name: "search_wiki",
    description: "Search the wiki",
    params: [
      { name: "query", required: true, type: "string" },
      { name: "limit", required: false, type: "integer" },
    ],
  },
  { name: "read_page", description: "", params: [{ name: "slug", required: true, type: "string" }] },
  {
    name: "create_page",
    description: "",
    params: [
      { name: "title", required: true, type: "string" },
      { name: "body", required: true, type: "string" },
      { name: "folder", required: false, type: "string" },
    ],
  },
  { name: "append_section", description: "", params: [{ name: "slug", required: true, type: "string" }, { name: "text", required: true, type: "string" }] },
];
const ROLES = ["search", "read", "create", "append", "archive", "delete", "rename", "move"];
const PROPOSED = {
  search: { tool: "search_wiki", args: { query: "query" } },
  read: { tool: "read_page", args: { slug: "ref" } },
  create: { tool: "create_page", args: { title: "title", body: "content" } },
  append: null,
  archive: null,
  delete: null,
  rename: null,
  move: null,
};
const VIEW = {
  tools: TOOLS,
  roles: ROLES,
  proposed: PROPOSED,
  saved: null,
  problems: {},
  sources: ["query", "ref", "title", "content", "to"],
};
const ROLES_PATH = "/ltm/sources/hermes/roles";

beforeEach(() => {
  H.gets = { "/brain": STATE };
  H.getCalls = [];
  H.posts = [];
  H.puts = [];
  H.answers = {};
  window.history.replaceState({}, "", "/settings");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => cleanup());

const flush = () =>
  act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });

/* ======================================================================= */
describe("1. the name is Memory profile", () => {
  it("the Settings section, its empty option, the project select and the drawer line say it", async () => {
    // Settings: the section component.
    const { container } = render(<BrainProfiles />);
    await screen.findByTestId("brain-profiles", {}, W);
    const empty = screen.getByTestId("brain-global") as HTMLSelectElement;
    expect(empty.options[0].textContent).toBe("No memory profile");
    expect(container.textContent).not.toMatch(/brain/i);
    cleanup();

    // The project page's select.
    render(<ProjectBrainSelect value="" onChange={() => {}} />);
    expect(await screen.findByLabelText("Memory profile", {}, W)).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/brain/i);
    cleanup();

    // The chat drawer's line.
    H.gets["/brain/effective?project_id=p1"] = { profile: NOTION, source: "project" };
    render(<ChatBrainLine projectId="p1" />);
    const line = await screen.findByTestId("chat-brain-line", {}, W);
    expect(line.textContent).toBe("Memory profile: Notion (this project). Change it");
  });

  it("the Settings row reads Memory profile, and a search for 'brain profile' still finds it", async () => {
    H.gets["/settings/schema"] = {
      groups: [{ id: "memory", label: "Memory & you", description: "Who you are." }],
      settings: [{ key: "brain_profile", label: "Memory profile", group: "memory", section: "", type: "string" }],
      secrets: [],
    };
    H.gets["/settings/values"] = { values: { brain_profile: "bp1" } };
    H.gets["/config/ledger"] = { changes: [] };
    window.history.replaceState({}, "", "/settings?section=memory");
    render(<SettingsHome />);
    const sub = await screen.findByTestId("settings-sub-memory-brain-profile", {}, W);
    expect(within(sub).getByText("Memory profile")).toBeTruthy();
    expect(sub.textContent).not.toMatch(/brain/i);
    cleanup();

    render(<SettingsHome />);
    fireEvent.change(await screen.findByTestId("settings-search", {}, W), { target: { value: "brain profile" } });
    expect(await screen.findByTestId("settings-sub-memory-brain-profile", {}, W)).toBeTruthy();
  });
});

/* ======================================================================= */
async function openEditor(view: unknown = VIEW) {
  H.gets[ROLES_PATH] = view;
  render(<McpBaseRoles name="hermes" />);
  fireEvent.click(await screen.findByTestId("mcp-roles-open-hermes", {}, W));
  return screen.findByTestId("mcp-roles-form-hermes", {}, W);
}

const toolSel = (role: string) => screen.getByTestId(`mcp-role-tool-${role}`) as HTMLSelectElement;
const argSel = (role: string, p: string) => screen.getByTestId(`mcp-arg-${role}-${p}`) as HTMLSelectElement;

describe("2. an MCP memory base's tool roles", () => {
  it("the editor starts from the proposal when nothing was confirmed, and says the rule first", async () => {
    await openEditor();
    expect(screen.getByTestId("mcp-roles-intro").textContent).toBe(ROLES_INTRO);
    expect(toolSel("search").value).toBe("search_wiki");
    expect(argSel("search", "query").value).toBe("query");
    expect(argSel("search", "limit").value).toBe("");
    expect(toolSel("create").value).toBe("create_page");
    expect(argSel("create", "body").value).toBe("content");
    expect(toolSel("append").value).toBe("");
    // The role words a person reads.
    expect(within(screen.getByTestId("mcp-role-append")).getByText("Add to an existing note")).toBeTruthy();
    expect(within(screen.getByTestId("mcp-role-search")).getByText("Find notes")).toBeTruthy();
  });

  it("the saved choice wins over the proposal", async () => {
    await openEditor({
      ...VIEW,
      saved: { search: { tool: "search_wiki", args: { query: "title" } }, append: { tool: "append_section", args: { slug: "ref", text: "content" } } },
    });
    expect(argSel("search", "query").value).toBe("title");
    expect(toolSel("append").value).toBe("append_section");
    // A role the saved choice leaves out is not used, whatever was proposed.
    expect(toolSel("read").value).toBe("");
    expect(toolSel("create").value).toBe("");
  });

  it("a required argument offers no 'Leave empty'; an optional one does", async () => {
    await openEditor();
    const required = Array.from(argSel("search", "query").options).map((o) => o.textContent);
    expect(required).not.toContain("Leave empty");
    expect(within(screen.getByTestId("mcp-role-args-search")).getByText(/required/)).toBeTruthy();
    const optional = Array.from(argSel("search", "limit").options).map((o) => o.textContent);
    expect(optional[0]).toBe("Leave empty");
    expect(optional).toEqual([
      "Leave empty",
      "The search words",
      "The note's reference",
      "The note's title",
      "The note's text",
      "The new name or folder",
      "A fixed value…",
    ]);
  });

  it("a newly chosen tool with an unset required argument cannot be saved, and says which", async () => {
    await openEditor();
    fireEvent.change(toolSel("append"), { target: { value: "append_section" } });
    fireEvent.click(screen.getByTestId("mcp-roles-save-hermes"));
    expect(await screen.findByText("Choose where “slug” comes from for add to an existing note.")).toBeTruthy();
    expect(H.puts).toEqual([]);
  });

  it("a fixed value has its own box, and the PUT body is exact", async () => {
    H.answers[`PUT ${ROLES_PATH}`] = {
      ...VIEW,
      saved: { search: { tool: "search_wiki", args: { query: "query", limit: { const: "5" } } } },
    };
    await openEditor();
    fireEvent.change(argSel("search", "limit"), { target: { value: "const" } });
    const box = await screen.findByTestId("mcp-arg-const-search-limit");
    fireEvent.change(box, { target: { value: "5" } });
    fireEvent.change(toolSel("read"), { target: { value: "" } });
    fireEvent.change(toolSel("append"), { target: { value: "append_section" } });
    fireEvent.change(argSel("append", "slug"), { target: { value: "ref" } });
    fireEvent.change(argSel("append", "text"), { target: { value: "content" } });
    fireEvent.change(toolSel("move"), { target: { value: "create_page" } });
    fireEvent.change(argSel("move", "title"), { target: { value: "to" } });
    fireEvent.change(argSel("move", "body"), { target: { value: "const" } });
    fireEvent.change(screen.getByTestId("mcp-arg-const-move-body"), { target: { value: "moved" } });
    fireEvent.click(screen.getByTestId("mcp-roles-save-hermes"));
    await waitFor(() => expect(H.puts).toHaveLength(1));
    expect(H.puts[0]).toEqual({
      path: ROLES_PATH,
      body: {
        roles: {
          search: { tool: "search_wiki", args: { query: "query", limit: { const: "5" } } },
          read: null,
          create: { tool: "create_page", args: { title: "title", body: "content" } },
          append: { tool: "append_section", args: { slug: "ref", text: "content" } },
          archive: null,
          delete: null,
          rename: null,
          move: { tool: "create_page", args: { title: "to", body: { const: "moved" } } },
        },
      },
    });
    // The view after the save: the form closes and the badge says it is set up.
    await waitFor(() => expect(screen.queryByTestId("mcp-roles-form-hermes")).toBeNull());
    expect(screen.getByTestId("mcp-roles-badge-hermes").textContent).toBe("Tools set up");
  });

  it("each role's problems are said under it, in the warn tone", async () => {
    await openEditor({ ...VIEW, problems: { create: ["create_page needs a title the server accepts."] } });
    const p = screen.getByTestId("mcp-role-problems-create");
    expect(p.textContent).toBe("create_page needs a title the server accepts.");
    expect(p.className).toContain("text-tone-warn");
    expect(screen.queryByTestId("mcp-role-problems-search")).toBeNull();
  });

  it("a 400 shows the daemon's sentence and keeps the form open", async () => {
    H.answers[`PUT ${ROLES_PATH}`] = new H.FakeApiError("search_wiki takes no argument named slug.", 400);
    await openEditor();
    fireEvent.click(screen.getByTestId("mcp-roles-save-hermes"));
    expect(await screen.findByText("search_wiki takes no argument named slug.")).toBeTruthy();
    expect(screen.getByTestId("mcp-roles-form-hermes")).toBeTruthy();
    expect(screen.getByTestId("mcp-roles-badge-hermes").textContent).toBe("Not set up yet");
  });

  it("a 404 (not an MCP base, or an older Iron Jarvis) draws nothing", async () => {
    H.gets[ROLES_PATH] = new H.FakeApiError("Not Found", 404);
    const { container } = render(<McpBaseRoles name="hermes" />);
    await waitFor(() => expect(H.getCalls).toContain(ROLES_PATH));
    await flush();
    expect(container.innerHTML).toBe("");
  });

  it("the row's badge: 'Not set up yet' until confirmed, then 'Tools set up'", async () => {
    H.gets[ROLES_PATH] = VIEW;
    render(<McpBaseRoles name="hermes" />);
    expect((await screen.findByTestId("mcp-roles-badge-hermes", {}, W)).textContent).toBe("Not set up yet");
    cleanup();
    H.gets[ROLES_PATH] = { ...VIEW, saved: { search: PROPOSED.search } };
    render(<McpBaseRoles name="hermes" />);
    expect((await screen.findByTestId("mcp-roles-badge-hermes", {}, W)).textContent).toBe("Tools set up");
  });

  it("the Long-term list puts it on an MCP base's row only (by the record's kind)", async () => {
    H.gets["/ltm/sources"] = {
      sources: [
        { name: "hermes", kind: "mcp", path: "http://127.0.0.1:9/mcp" },
        { name: "notes", kind: "markdown", path: "C:/notes" },
      ],
      active: ["brain", "hermes", "notes"],
    };
    H.gets[ROLES_PATH] = VIEW;
    render(<LongTerm />);
    const row = await screen.findByTestId("ltm-source-row-hermes", {}, W);
    expect(await within(row).findByTestId("mcp-roles-badge-hermes", {}, W)).toBeTruthy();
    expect(within(row).getByRole("button", { name: "Set up tools" })).toBeTruthy();
    expect(within(screen.getByTestId("ltm-source-row-notes")).queryByText("Set up tools")).toBeNull();
    expect(H.getCalls.filter((p) => p.endsWith("/roles"))).toEqual([ROLES_PATH]);
  });

  it("the decoders: unknown roles and sources die; the body keeps only the tool's own arguments", () => {
    const v = decodeRolesView({
      ...VIEW,
      roles: [...ROLES, "teleport"],
      sources: ["query", "secret"],
      proposed: { search: { tool: "search_wiki", args: { query: "query", ghost: "ref", bad: 3 } } },
    })!;
    expect(v.roles).toEqual(ROLES);
    expect(v.sources).toEqual(["query"]);
    expect(v.proposed.search).toEqual({ tool: "search_wiki", args: { query: "query", ghost: "ref" } });
    const body = rolesBody(v, startingChoice(v));
    expect(body.roles.search).toEqual({ tool: "search_wiki", args: { query: "query" } });
    expect(decodeRolesView({ roles: [] })).toBeNull();
    expect(decodeRolesView("x")).toBeNull();
  });
});

/* ======================================================================= */
async function renderProfiles() {
  render(<BrainProfiles />);
  return screen.findByTestId("brain-row-bp1", {}, W);
}

describe("3. Check a memory profile, and Try a test save", () => {
  it("Check lists the steps, each in its tone", async () => {
    H.answers["POST /brain/profiles/bp1/check"] = {
      steps: [
        { step: "reach", ok: true, words: "Notion answered." },
        { step: "search", ok: false, words: "A search found nothing." },
        { step: "guide", ok: null, words: "No operating guide is set." },
      ],
    };
    const row = await renderProfiles();
    fireEvent.click(within(row).getByRole("button", { name: "Check" }));
    const list = await within(row).findByTestId("brain-check-steps-bp1", {}, W);
    expect(H.posts.map((p) => p.path)).toEqual(["/brain/profiles/bp1/check"]);
    const reach = within(list).getByTestId("brain-check-step-reach");
    const search = within(list).getByTestId("brain-check-step-search");
    const guide = within(list).getByTestId("brain-check-step-guide");
    expect(reach.textContent).toContain("Notion answered.");
    expect(reach.className).toContain("text-tone-success");
    expect(search.className).toContain("text-tone-warn");
    expect(guide.className).toContain("text-zinc-500");
    expect(guide.textContent).toContain("not checked");
  });

  it("Try a test save takes two presses, then shows its words", async () => {
    H.answers["POST /brain/profiles/bp1/test-save"] = { ok: true, words: "Saved a test note and read it back." };
    const row = await renderProfiles();
    fireEvent.click(within(row).getByRole("button", { name: "Try a test save" }));
    expect(H.posts).toEqual([]); // the first press only arms it
    fireEvent.click(within(row).getByRole("button", { name: "Save a test note?" }));
    const out = await within(row).findByTestId("brain-test-save-result-bp1", {}, W);
    expect(out.textContent).toBe("Saved a test note and read it back.");
    expect(out.className).toContain("text-tone-success");
    expect(H.posts.map((p) => p.path)).toEqual(["/brain/profiles/bp1/test-save"]);
  });

  it("a test save that did not land reads in the warn tone; a 409 says its sentence", async () => {
    H.answers["POST /brain/profiles/bp1/test-save"] = { ok: false, words: "Notion took the note but it could not be read back." };
    const row = await renderProfiles();
    fireEvent.click(within(row).getByRole("button", { name: "Try a test save" }));
    fireEvent.click(within(row).getByRole("button", { name: "Save a test note?" }));
    const out = await within(row).findByTestId("brain-test-save-result-bp1", {}, W);
    expect(out.className).toContain("text-tone-warn");
    cleanup();

    H.answers["POST /brain/profiles/bp1/test-save"] = new H.FakeApiError("This profile never saves (Save to: never).", 409);
    const row2 = await renderProfiles();
    fireEvent.click(within(row2).getByRole("button", { name: "Try a test save" }));
    fireEvent.click(within(row2).getByRole("button", { name: "Save a test note?" }));
    const err = await within(row2).findByTestId("brain-test-save-error-bp1", {}, W);
    expect(err.textContent).toBe("This profile never saves (Save to: never).");
    expect(err.className).toContain("text-tone-warn");
  });

  it("a 404 (an older Iron Jarvis) hides both buttons", async () => {
    H.answers["POST /brain/profiles/bp1/check"] = new H.FakeApiError("Not Found", 404);
    const row = await renderProfiles();
    fireEvent.click(within(row).getByRole("button", { name: "Check" }));
    await waitFor(() => expect(within(row).queryByRole("button", { name: "Check" })).toBeNull());
    expect(within(row).queryByRole("button", { name: "Try a test save" })).toBeNull();
  });

  it("the decoders: unknown steps and empty words die", () => {
    expect(
      decodeCheck({ steps: [{ step: "reach", ok: "yes", words: "x" }, { step: "fly", ok: true, words: "y" }, { step: "save", ok: true, words: " " }] }),
    ).toEqual([{ step: "reach", ok: null, words: "x" }]);
    expect(decodeCheck({})).toBeNull();
    expect(decodeTestSave({ ok: "true" })).toBeNull();
    expect(decodeTestSave({ ok: true })!.words).toBe("The test note was saved.");
  });
});

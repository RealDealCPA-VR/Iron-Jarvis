/**
 * v1.408.0 review — the memory-profile SEAM. The screen (Set up tools on an
 * MCP memory base; Check / Try a test save on a memory profile) was built
 * against stand-ins of the contract. This file feeds what the REAL daemon
 * answered (recorded by tests/test_memory_profile_seams_v1408.py into
 * fixtures/memory-profile-seams-v1408.json, a real McpBrainConnector over the
 * user's wiki tools) through the real components.
 *
 * Defects this review found, each red here first:
 *   D1 the badge read GET /roles (a live tools/list of the server, up to 20 s)
 *      on every Memory visit, while the listing already says `tools_set_up`;
 *      and the editor opened on that mount-time list, never re-read.
 *   D2 a role's problems (computed by the daemon for the proposal/saved
 *      mapping) stayed under it after the user changed that role — "'type' is
 *      required" beside a fixed type the user had just typed.
 *   D3 a REQUIRED argument given a fixed value left blank was saved as "".
 *   D4 a server that did not answer had its confirmed tools labelled "the
 *      server no longer offers it".
 *   D5 a profile deleted elsewhere (404 "That profile no longer exists.") hid
 *      Check and Try a test save for EVERY profile, as if Iron Jarvis were old.
 *   D6 saving with every role on "Not used" said "Jarvis now uses these tools".
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import seams from "./fixtures/memory-profile-seams-v1408.json";

type Answer = { status: number; body: unknown };
const S = seams as unknown as Record<string, Answer>;

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

/** A recorded answer as lib/api would hand it over: the body, or an ApiError
 *  whose message is the flattened `detail`. */
function served(a: Answer): unknown {
  if (a.status >= 400) {
    const d = (a.body as { detail?: unknown })?.detail;
    return new H.FakeApiError(typeof d === "string" ? d : `${a.status}`, a.status);
  }
  return a.body;
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
    const v = H.answers[`POST ${path}`];
    if (v instanceof Error) throw v;
    return v ?? {};
  },
  put: async (path: string, body?: unknown) => {
    H.puts.push({ path, body });
    const v = H.answers[`PUT ${path}`];
    if (v instanceof Error) throw v;
    return v ?? {};
  },
  patch: async () => ({}),
  del: async () => ({}),
}));
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ online: true, checking: false, health: { providers: [] }, refresh: vi.fn() }),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/memory",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(window.location.search),
}));

import { LongTerm } from "@/components/memory/LongTerm";
import { McpBaseRoles } from "@/components/memory/McpBaseRoles";
import { BrainProfiles } from "@/components/settings/BrainProfiles";

const W = { timeout: 10_000 };
const BASE = "hermes-brain";
const ROLES = `/ltm/sources/${BASE}/roles`;

beforeEach(() => {
  H.gets = {};
  H.getCalls = [];
  H.posts = [];
  H.puts = [];
  H.answers = {};
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => cleanup());

const rolesGets = () => H.getCalls.filter((p) => p.endsWith("/roles"));
const toolSel = (role: string) => screen.getByTestId(`mcp-role-tool-${role}`) as HTMLSelectElement;
const argSel = (role: string, p: string) => screen.getByTestId(`mcp-arg-${role}-${p}`) as HTMLSelectElement;

async function openEditor(view: Answer = S.roles_unconfirmed, setUp?: boolean) {
  H.gets[ROLES] = served(view);
  render(<McpBaseRoles name={BASE} setUp={setUp} />);
  fireEvent.click(await screen.findByTestId(`mcp-roles-open-${BASE}`, {}, W));
  return screen.findByTestId(`mcp-roles-form-${BASE}`, {}, W);
}

describe("D1: the badge comes from the listing; the tools are read when the editor opens", () => {
  it("the Long-term list says set up / not set up from tools_set_up, with no live tool listing", async () => {
    H.gets["/ltm/sources"] = S.listing_confirmed.body;
    H.gets[ROLES] = served(S.put_confirmed);
    render(<LongTerm />);
    const row = await screen.findByTestId(`ltm-source-row-${BASE}`, {}, W);
    expect((await within(row).findByTestId(`mcp-roles-badge-${BASE}`, {}, W)).textContent).toBe("Tools set up");
    expect(within(screen.getByTestId("ltm-source-row-notes")).queryByText("Set up tools")).toBeNull();
    expect(rolesGets()).toEqual([]);
    cleanup();

    H.getCalls = [];
    H.gets["/ltm/sources"] = S.listing_unconfirmed.body;
    render(<LongTerm />);
    const row2 = await screen.findByTestId(`ltm-source-row-${BASE}`, {}, W);
    expect((await within(row2).findByTestId(`mcp-roles-badge-${BASE}`, {}, W)).textContent).toBe("Not set up yet");
    expect(rolesGets()).toEqual([]);
  });

  it("a memory server's row says its kind in words and where it is (never a raw kind or a dash)", async () => {
    H.gets["/ltm/sources"] = S.listing_confirmed.body;
    render(<LongTerm />);
    const row = await screen.findByTestId(`ltm-source-row-${BASE}`, {}, W);
    expect(within(row).getByText("memory server")).toBeTruthy();
    expect(within(row).queryByText("mcp")).toBeNull();
    expect(within(row).getByText("http://wiki.invalid/mcp")).toBeTruthy();
    expect(within(screen.getByTestId("ltm-source-row-notes")).getByText("folder")).toBeTruthy();
  });

  it("every press of Set up tools reads the server's tools again", async () => {
    await openEditor(S.roles_unconfirmed, false);
    expect(rolesGets()).toEqual([ROLES]);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(await screen.findByTestId(`mcp-roles-open-${BASE}`, {}, W));
    await screen.findByTestId(`mcp-roles-form-${BASE}`, {}, W);
    expect(rolesGets()).toEqual([ROLES, ROLES]);
  });

  it("a 404 when opening (not an MCP base any more) draws nothing", async () => {
    H.gets[ROLES] = served(S.roles_not_mcp);
    const { container } = render(<McpBaseRoles name={BASE} setUp={false} />);
    fireEvent.click(await screen.findByTestId(`mcp-roles-open-${BASE}`, {}, W));
    await waitFor(() => expect(container.innerHTML).toBe(""));
  });
});

describe("the real proposal for the user's wiki, through the editor", () => {
  it("D2: the daemon's problems leave a role once the user changes it; the PUT is one the daemon took", async () => {
    await openEditor();
    // The real proposal for create_page misses the wiki's required type and tags.
    expect(toolSel("create").value).toBe("create_page");
    expect(screen.getByTestId("mcp-role-problems-create").textContent).toContain("'type' is required");
    fireEvent.change(argSel("create", "type"), { target: { value: "const" } });
    fireEvent.change(screen.getByTestId("mcp-arg-const-create-type"), { target: { value: "concept" } });
    await waitFor(() => expect(screen.queryByTestId("mcp-role-problems-create")).toBeNull());
    fireEvent.change(argSel("create", "tags"), { target: { value: "const" } });
    fireEvent.change(screen.getByTestId("mcp-arg-const-create-tags"), { target: { value: "inbox, notes" } });

    H.answers[`PUT ${ROLES}`] = served(S.put_confirmed);
    fireEvent.click(screen.getByTestId(`mcp-roles-save-${BASE}`));
    await waitFor(() => expect(H.puts).toHaveLength(1));
    const sent = (H.puts[0].body as { roles: Record<string, unknown> }).roles;
    const saved = (S.put_confirmed.body as { saved: Record<string, unknown> }).saved;
    // The create mapping the screen sends is exactly the one the real daemon saved.
    expect(sent.create).toEqual(saved.create);
    expect(sent.search).toEqual(saved.search);
    await waitFor(() => expect(screen.queryByTestId(`mcp-roles-form-${BASE}`)).toBeNull());
    expect(screen.getByTestId(`mcp-roles-badge-${BASE}`).textContent).toBe("Tools set up");
  });

  it("the problems come back when the role is put back as it was", async () => {
    await openEditor();
    fireEvent.change(argSel("create", "summary"), { target: { value: "ref" } });
    await waitFor(() => expect(screen.queryByTestId("mcp-role-problems-create")).toBeNull());
    fireEvent.change(argSel("create", "summary"), { target: { value: "title" } });
    expect((await screen.findByTestId("mcp-role-problems-create")).textContent).toContain("'tags' is required");
  });

  it("every control is reached by its label, even on a base whose name has a space", async () => {
    H.gets["/ltm/sources/My%20Wiki/roles"] = served(S.roles_unconfirmed);
    render(<McpBaseRoles name="My Wiki" setUp={false} />);
    fireEvent.click(await screen.findByTestId("mcp-roles-open-My Wiki", {}, W));
    await screen.findByTestId("mcp-roles-form-My Wiki", {}, W);
    expect((screen.getByLabelText("Save a new note") as HTMLSelectElement).value).toBe("create_page");
    expect(screen.getByLabelText(/^query/)).toBe(screen.getByTestId("mcp-arg-search-query"));
    expect(screen.getByLabelText(/^summary/)).toBe(screen.getByTestId("mcp-arg-create-summary"));
  });

  it("D3: a required argument with a blank fixed value is not saved", async () => {
    await openEditor();
    fireEvent.change(argSel("create", "type"), { target: { value: "const" } });
    fireEvent.change(argSel("create", "tags"), { target: { value: "const" } });
    fireEvent.change(screen.getByTestId("mcp-arg-const-create-tags"), { target: { value: "inbox" } });
    fireEvent.click(screen.getByTestId(`mcp-roles-save-${BASE}`));
    expect(await screen.findByText("Type the fixed value for “type” for save a new note.")).toBeTruthy();
    expect(H.puts).toEqual([]);
  });

  it("the daemon's 400 sentence is shown word for word and the form stays open", async () => {
    await openEditor();
    fireEvent.change(argSel("create", "type"), { target: { value: "const" } });
    fireEvent.change(screen.getByTestId("mcp-arg-const-create-type"), { target: { value: "concept" } });
    fireEvent.change(argSel("create", "tags"), { target: { value: "const" } });
    fireEvent.change(screen.getByTestId("mcp-arg-const-create-tags"), { target: { value: "x" } });
    H.answers[`PUT ${ROLES}`] = served(S.put_refused);
    fireEvent.click(screen.getByTestId(`mcp-roles-save-${BASE}`));
    const detail = (S.put_refused.body as { detail: string }).detail;
    expect(await screen.findByText(detail)).toBeTruthy();
    expect(screen.getByTestId(`mcp-roles-form-${BASE}`)).toBeTruthy();
  });

  it("D4: a server that did not answer says so, and never calls its tools gone", async () => {
    await openEditor(S.roles_unreachable, true);
    const err = (S.roles_unreachable.body as { error: string }).error;
    expect(screen.getByText(err)).toBeTruthy();
    expect(toolSel("create").value).toBe("create_page");
    expect(screen.getByTestId(`mcp-roles-form-${BASE}`).textContent).not.toMatch(/no longer offers/);
  });

  it("D6: saving with nothing chosen does not say the tools are in use", async () => {
    await openEditor(S.put_confirmed, true);
    for (const r of ["search", "read", "create", "append", "archive"]) {
      fireEvent.change(toolSel(r), { target: { value: "" } });
    }
    H.answers[`PUT ${ROLES}`] = served(S.put_nothing);
    fireEvent.click(screen.getByTestId(`mcp-roles-save-${BASE}`));
    await waitFor(() => expect(H.puts).toHaveLength(1));
    await waitFor(() => expect(screen.queryByTestId(`mcp-roles-form-${BASE}`)).toBeNull());
    expect(screen.getByTestId(`mcp-roles-badge-${BASE}`).textContent).toBe("Not set up yet");
    expect(document.body.textContent).not.toMatch(/now uses these tools/);
    expect(document.body.textContent).toMatch(/only search and read/);
  });
});

/* ======================================================================= */
const PROFILE = (id: string, name: string) => ({
  id,
  name,
  primary: BASE,
  search_first: true,
  save_to: "primary",
  prefer_update: true,
  on_conflict: "ask",
  guide_ref: "",
  skills: [],
  repo_routing: false,
});

describe("Check and Try a test save, on the real answers", () => {
  beforeEach(() => {
    H.gets["/brain"] = {
      profiles: [PROFILE("bp1", "Wiki"), PROFILE("bp2", "Work")],
      selected: "bp1",
      bases: [{ name: BASE, label: BASE, kind: "mcp", available: true }],
      pending: 0,
    };
  });

  it("an unconfirmed base's Check: the save step is a problem that names Set up tools", async () => {
    H.answers["POST /brain/profiles/bp1/check"] = served(S.check_unconfirmed);
    render(<BrainProfiles />);
    const row = await screen.findByTestId("brain-row-bp1", {}, W);
    fireEvent.click(within(row).getByRole("button", { name: "Check" }));
    const save = await within(row).findByTestId("brain-check-step-save", {}, W);
    expect(save.dataset.tone).toBe("warn");
    expect(save.textContent).toContain("Set up tools");
    expect(within(row).getByTestId("brain-check-step-reach").dataset.tone).toBe("ok");
    expect(within(row).getByTestId("brain-check-step-guide").dataset.tone).toBe("neutral");
  });

  it("a confirmed base's test save shows the daemon's words in the success tone", async () => {
    H.answers["POST /brain/profiles/bp1/test-save"] = served(S.test_save_ok);
    render(<BrainProfiles />);
    const row = await screen.findByTestId("brain-row-bp1", {}, W);
    fireEvent.click(within(row).getByRole("button", { name: "Try a test save" }));
    fireEvent.click(within(row).getByRole("button", { name: "Save a test note?" }));
    const out = await within(row).findByTestId("brain-test-save-result-bp1", {}, W);
    expect(out.textContent).toBe((S.test_save_ok.body as { words: string }).words);
    expect(out.dataset.tone).toBe("ok");
  });

  it("D5: a profile deleted elsewhere says so, and every other profile keeps its Check", async () => {
    H.answers["POST /brain/profiles/bp1/check"] = served(S.check_gone_profile);
    render(<BrainProfiles />);
    const row = await screen.findByTestId("brain-row-bp1", {}, W);
    fireEvent.click(within(row).getByRole("button", { name: "Check" }));
    const err = await within(row).findByTestId("brain-check-error-bp1", {}, W);
    expect(err.textContent).toBe("That profile no longer exists.");
    expect(within(screen.getByTestId("brain-row-bp2")).getByRole("button", { name: "Check" })).toBeTruthy();
  });
});

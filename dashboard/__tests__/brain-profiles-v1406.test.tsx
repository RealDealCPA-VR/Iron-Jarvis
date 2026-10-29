/**
 * v1.406.0 — Brain profiles on the dashboard.
 *
 *   - lib/brainProfiles: the decoders (whitelists that tolerate anything) and
 *     the words (effective, receipt line, receipt detail).
 *   - Settings › Memory & you › Brain profile: the app-wide choice through
 *     PUT /settings/values, a new profile (POST), an edit (PATCH, only the
 *     changed fields), a 400 sentence shown, Delete in two presses, an
 *     unreachable base said, and the row drawn only when the schema knows
 *     `brain_profile` (not twice: CONTROLLED_IN_SECTION).
 *   - The project page's select (component; the page itself is driven in
 *     brain-profiles-project-v1406).
 *   - The turn receipt: saved (accent), unsaved (warn, first), not confirmed,
 *     null says nothing; the expanded detail.
 *   - The chat drawer's quiet line, and Memory's "Unsaved changes" card:
 *     Try again and Discard (two presses).
 *   - 404 (an older Iron Jarvis) renders nothing anywhere.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { readSrc } from "./helpers/dashGuard";

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
    patches: [] as { path: string; body: unknown }[],
    dels: [] as string[],
    answers: {} as Record<string, unknown>,
  };
});

/** An answer: a value, an Error to throw, or a function of the body. */
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
  patch: async (path: string, body?: unknown) => {
    H.patches.push({ path, body });
    return answer(`PATCH ${path}`, body);
  },
  del: async (path: string) => {
    H.dels.push(path);
    return answer(`DELETE ${path}`);
  },
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
vi.mock("@/components/memory/MemorySurface", () => ({ MemorySurface: () => <div data-testid="memory-surface" /> }));
vi.mock("@/components/memory/MemoryReview", () => ({ MemoryReview: () => null }));

import {
  CONFLICT_WORDS,
  SAVE_TO_WORDS,
  brainApplies,
  changedFields,
  decodeBrainEffective,
  decodeBrainPending,
  decodeBrainProfile,
  decodeBrainReceipt,
  decodeBrainState,
  draftOf,
  effectiveWords,
  receiptBrainDetail,
  receiptBrainLine,
} from "@/lib/brainProfiles";
import { BrainProfiles } from "@/components/settings/BrainProfiles";
import { SettingsHome, CONTROLLED_IN_SECTION } from "@/components/settings/SettingsHome";
import { ProjectBrainSelect } from "@/components/project/ProjectBrainSelect";
import { TurnReceipt, receiptWantsAttention } from "@/components/chat/TurnReceipt";
import { ChatBrainLine } from "@/components/chat/ChatBrainLine";
import { BrainUnsaved } from "@/components/memory/BrainUnsaved";
import MemoryPage from "@/app/memory/page";

const NOTION = {
  id: "bp1",
  name: "Notion",
  primary: "notion",
  search_first: true,
  save_to: "primary",
  prefer_update: true,
  on_conflict: "ask",
  guide_ref: "Operating guide",
  skills: ["tax-advisement"],
  repo_routing: false,
  created_at: "2026-10-29T10:00:00Z",
  updated_at: "2026-10-29T10:00:00Z",
};
const OBSIDIAN = { ...NOTION, id: "bp2", name: "Obsidian vault", primary: "vault", skills: [] };
const BASES = [
  { name: "notion", label: "Notion", kind: "mcp", available: true },
  { name: "vault", label: "Obsidian vault", kind: "folder", available: false },
  { name: "brain", label: "Iron Jarvis memory", kind: "builtin", available: null },
];
const STATE = { profiles: [NOTION, OBSIDIAN], selected: "bp1", bases: BASES, pending: 0 };

const W = { timeout: 10_000 };

beforeEach(() => {
  H.gets = { "/brain": STATE, "/skills": { skills: [{ name: "tax-advisement" }, { name: "pixio-skill" }, { name: "docs" }] } };
  H.getCalls = [];
  H.posts = [];
  H.puts = [];
  H.patches = [];
  H.dels = [];
  H.answers = {};
  window.history.replaceState({}, "", "/settings");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => cleanup());

/* ======================================================================= */
describe("lib/brainProfiles — decoders", () => {
  it("a profile keeps only the contract's keys, and tolerates junk", () => {
    const p = decodeBrainProfile({ ...NOTION, secret: "sk-1", save_to: "everywhere", on_conflict: 3, skills: ["a", 2, "a", ""] })!;
    expect(Object.keys(p).sort()).toEqual(
      [
        "created_at", "guide_ref", "id", "name", "on_conflict", "prefer_update", "primary", "repo_routing",
        "save_to", "search_first", "skills", "updated_at",
      ].sort(),
    );
    expect(p.save_to).toBe("ask");
    expect(p.on_conflict).toBe("ask");
    expect(p.skills).toEqual(["a"]);
    expect(decodeBrainProfile({ name: "no id" })).toBeNull();
    expect(decodeBrainProfile("x")).toBeNull();
    // Missing booleans read as off, never as "on by accident".
    expect(decodeBrainProfile({ id: "x" })!.search_first).toBe(false);
  });

  it("the state needs a profiles list; bases are three-valued", () => {
    expect(decodeBrainState({})).toBeNull();
    expect(decodeBrainState(null)).toBeNull();
    const s = decodeBrainState({ ...STATE, bases: [...BASES, { label: "no name" }, { name: "x", available: "yes" }] })!;
    expect(s.profiles.map((p) => p.id)).toEqual(["bp1", "bp2"]);
    expect(s.bases.map((b) => b.available)).toEqual([true, false, null, null]);
    expect(s.selected).toBe("bp1");
  });

  it("effective: a profile-less project/app reads as none; off drops the profile", () => {
    expect(decodeBrainEffective({ profile: null, source: "project" })).toEqual({ profile: null, source: "none" });
    expect(decodeBrainEffective({ profile: NOTION, source: "off" })!.profile).toBeNull();
    expect(decodeBrainEffective({ profile: NOTION, source: "weird" })!.source).toBe("none");
  });

  it("pending needs a list; rows need an id", () => {
    expect(decodeBrainPending({})).toBeNull();
    const rows = decodeBrainPending({ pending: [{ id: "u1", title: "Plan", base: "Notion", extra: 1 }, { title: "no id" }] })!;
    expect(rows).toHaveLength(1);
    expect(Object.keys(rows[0]).sort()).toEqual(["base", "created_at", "error", "id", "preview", "profile_name", "title"]);
  });

  it("the receipt: null for null, rows whitelisted, verified only for a real true", () => {
    expect(decodeBrainReceipt(null)).toBeNull();
    expect(decodeBrainReceipt({})).toBeNull();
    const b = decodeBrainReceipt({
      name: "Notion",
      base: "Notion",
      searched: 3.7,
      guide: "maybe",
      saved: [{ title: "Q4 plan", ref: "notion://1", action: "updated", verified: "yes", x: 1 }, { ref: "" }],
      unsaved: [{ title: "Draft", reason: "the base did not answer" }, { reason: "no title" }],
      changed: [{ action: "archive", ref: "notion://2" }, { action: "" }],
      extra: "dropped",
    })!;
    expect(Object.keys(b).sort()).toEqual(["base", "changed", "guide", "name", "saved", "searched", "unsaved"]);
    expect(b.searched).toBe(3);
    expect(b.guide).toBe("");
    expect(b.saved).toEqual([{ title: "Q4 plan", ref: "notion://1", action: "updated", verified: false }]);
    expect(b.unsaved).toHaveLength(1);
    expect(b.changed).toHaveLength(1);
    expect(decodeBrainReceipt({ name: "Notion", searched: -1 })!.searched).toBeNull();
  });
});

describe("lib/brainProfiles — words", () => {
  const eff = (source: string, profile: unknown = NOTION) => decodeBrainEffective({ profile, source });

  it("effectiveWords names the profile and where its choice came from", () => {
    expect(effectiveWords(eff("project"))).toBe("Notion (this project)");
    expect(effectiveWords(eff("app"))).toBe("Notion (app default)");
    expect(effectiveWords(eff("off"))).toBe("None in this project");
    expect(effectiveWords(eff("none"))).toBe("");
    expect(effectiveWords(null)).toBe("");
    expect(brainApplies(eff("project"))).toBe(true);
    expect(brainApplies(eff("off"))).toBe(false);
  });

  const R = (over: Record<string, unknown>) =>
    decodeBrainReceipt({ name: "Notion", base: "Notion", searched: 2, guide: "loaded", saved: [], unsaved: [], changed: [], ...over });

  it("the collapsed line: unsaved FIRST in warn, else saved in the accent, else nothing", () => {
    const both = receiptBrainLine(
      R({
        saved: [{ title: "Q4 plan", ref: "r", action: "created", verified: true }],
        unsaved: [{ title: "Draft", reason: "the base did not answer" }],
      }),
    )!;
    expect(both.tone).toBe("warn");
    expect(both.text).toBe("Not saved yet: Draft. The base did not answer.");
    const saved = receiptBrainLine(R({ saved: [{ title: "Q4 plan", ref: "r", action: "created", verified: true }] }))!;
    expect(saved).toMatchObject({ tone: "accent", text: "Saved to Notion: Q4 plan" });
    const unconfirmed = receiptBrainLine(R({ saved: [{ title: "Q4 plan", ref: "r", action: "created", verified: false }] }))!;
    expect(unconfirmed.text).toBe("Saved to Notion: Q4 plan · not confirmed");
    expect(receiptBrainLine(R({ saved: [{ title: "A", verified: true }, { title: "B", verified: true }] }))!.text).toBe(
      "Saved to Notion: A (+1 more)",
    );
    // Searched alone does not show on the collapsed line.
    expect(receiptBrainLine(R({}))).toBeNull();
    expect(receiptBrainLine(null)).toBeNull();
  });

  it("the expanded detail: searched, guide, every note", () => {
    const d = receiptBrainDetail(
      R({
        searched: 4,
        guide: "unreadable",
        saved: [{ title: "Q4 plan", action: "updated", verified: true }],
        unsaved: [{ title: "Draft", reason: "offline" }],
        changed: [{ action: "archived", ref: "Old note" }],
      }),
    );
    expect(d.map((x) => x.text)).toEqual([
      "Searched Notion first · 4 notes",
      "Operating guide could not be read",
      "Updated in Notion: Q4 plan",
      "Not saved yet: Draft. Offline.",
      "Archived: Old note",
    ]);
    expect(d.map((x) => x.warn)).toEqual([false, true, false, true, false]);
    expect(receiptBrainDetail(R({ searched: 1, guide: "loaded" })).map((x) => x.text)).toEqual([
      "Searched Notion first · 1 note",
      "Operating guide loaded",
    ]);
    expect(receiptBrainDetail(R({ searched: 0, guide: "" }))[0].text).toBe("Searched Notion first · no notes found");
    expect(receiptBrainDetail(R({ searched: null, guide: "" }))).toEqual([]);
  });

  it("changedFields sends only what an edit changed", () => {
    const a = draftOf(decodeBrainProfile(NOTION)!);
    expect(changedFields(a, { ...a })).toEqual({});
    expect(changedFields(a, { ...a, name: "N2", skills: [] })).toEqual({ name: "N2", skills: [] });
  });
});

/* ======================================================================= */
async function renderSection() {
  render(<BrainProfiles />);
  return screen.findByTestId("brain-profiles", {}, W);
}

describe("Settings › Brain profile (the component)", () => {
  it("lists the profiles with their base, and says an unreachable one", async () => {
    await renderSection();
    expect(within(screen.getByTestId("brain-row-bp1")).getByText("Notion", { selector: "span.block.text-\\[12px\\]" })).toBeTruthy();
    expect(screen.getByTestId("brain-unreachable-bp2").textContent).toContain("not reachable right now");
    expect(screen.queryByTestId("brain-unreachable-bp1")).toBeNull();
    // The global select shows the app's choice; the structural floor is said.
    expect((screen.getByTestId("brain-global") as HTMLSelectElement).value).toBe("bp1");
    expect(screen.getByTestId("brain-structural").textContent).toMatch(/always asks you first/);
  });

  it("the app-wide choice saves through PUT /settings/values", async () => {
    await renderSection();
    fireEvent.change(screen.getByTestId("brain-global"), { target: { value: "bp2" } });
    await waitFor(() => expect(H.puts).toHaveLength(1));
    expect(H.puts[0]).toEqual({ path: "/settings/values", body: { values: { brain_profile: "bp2" } } });
    fireEvent.change(screen.getByTestId("brain-global"), { target: { value: "" } });
    await waitFor(() => expect(H.puts).toHaveLength(2));
    expect(H.puts[1].body).toEqual({ values: { brain_profile: "" } });
    expect(await screen.findByText(/No profile is used by default/)).toBeTruthy();
  });

  it("New profile: POSTs the whole form, skills chosen as chips", async () => {
    H.answers["POST /brain/profiles"] = { ...NOTION, id: "bp3", name: "Work" };
    await renderSection();
    fireEvent.click(screen.getByTestId("brain-new"));
    const form = await screen.findByTestId("brain-form");
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "Work" } });
    fireEvent.change(within(form).getByLabelText("Primary memory base"), { target: { value: "brain" } });
    fireEvent.click(within(form).getByLabelText("Send knowledge about code to the repository"));
    fireEvent.change(within(form).getByLabelText("Save new knowledge to"), { target: { value: "ask" } });
    fireEvent.change(within(form).getByLabelText("When memory and the conversation disagree"), {
      target: { value: "newest" },
    });
    fireEvent.change(within(form).getByLabelText("Operating guide"), { target: { value: "How I work" } });
    // Skills come from GET /skills; a chosen one is a chip with ×.
    const add = within(form).getByLabelText("Skills to load at the start");
    await waitFor(() => expect(within(add).getByText("pixio-skill")).toBeTruthy());
    fireEvent.change(add, { target: { value: "pixio-skill" } });
    fireEvent.change(add, { target: { value: "docs" } });
    fireEvent.click(within(form).getByRole("button", { name: "Remove docs" }));
    expect(within(screen.getByTestId("brain-skills")).getByText("pixio-skill")).toBeTruthy();
    fireEvent.click(within(form).getByTestId("brain-save"));
    await waitFor(() => expect(H.posts).toHaveLength(1));
    expect(H.posts[0]).toEqual({
      path: "/brain/profiles",
      body: {
        name: "Work",
        primary: "brain",
        search_first: true,
        save_to: "ask",
        prefer_update: true,
        on_conflict: "newest",
        guide_ref: "How I work",
        skills: ["pixio-skill"],
        repo_routing: true,
      },
    });
    expect(await screen.findByText("Made Work.")).toBeTruthy();
    // The list is read again after a save.
    expect(H.getCalls.filter((p) => p === "/brain").length).toBeGreaterThanOrEqual(2);
  });

  it("the operating guide's help says it is followed as instructions and nothing else is", async () => {
    await renderSection();
    fireEvent.click(screen.getByTestId("brain-new"));
    expect(await screen.findByText(/followed as instructions\. Everything else from memory is treated as information/)).toBeTruthy();
    expect(within(screen.getByTestId("brain-form")).getByRole("option", { name: SAVE_TO_WORDS.never })).toBeTruthy();
    expect(within(screen.getByTestId("brain-form")).getByRole("option", { name: CONFLICT_WORDS.brain })).toBeTruthy();
  });

  it("Edit PATCHes only the changed fields", async () => {
    H.answers["PATCH /brain/profiles/bp1"] = { ...NOTION, name: "Notion work" };
    await renderSection();
    fireEvent.click(screen.getByTestId("brain-edit-bp1"));
    const form = await screen.findByTestId("brain-form");
    expect((within(form).getByLabelText("Name") as HTMLInputElement).value).toBe("Notion");
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "Notion work" } });
    fireEvent.click(within(form).getByLabelText("Search it before answering"));
    fireEvent.click(within(form).getByTestId("brain-save"));
    await waitFor(() => expect(H.patches).toHaveLength(1));
    expect(H.patches[0]).toEqual({ path: "/brain/profiles/bp1", body: { name: "Notion work", search_first: false } });
    await waitFor(() => expect(screen.queryByTestId("brain-form")).toBeNull());
  });

  it("a 400 shows the daemon's one sentence and keeps the form open", async () => {
    H.answers["POST /brain/profiles"] = new H.FakeApiError("Pick a memory base that exists.", 400);
    await renderSection();
    fireEvent.click(screen.getByTestId("brain-new"));
    const form = await screen.findByTestId("brain-form");
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "X" } });
    fireEvent.click(within(form).getByTestId("brain-save"));
    expect(await within(form).findByText("Pick a memory base that exists.")).toBeTruthy();
    expect(screen.getByTestId("brain-form")).toBeTruthy();
  });

  it("Delete takes two presses, then says what it cleared", async () => {
    H.answers["DELETE /brain/profiles/bp1"] = { deleted: true, cleared: { app: true, projects: 2 } };
    await renderSection();
    const row = screen.getByTestId("brain-row-bp1");
    fireEvent.click(within(row).getByRole("button", { name: "Delete" }));
    expect(H.dels).toEqual([]); // the first press only arms it
    fireEvent.click(within(row).getByRole("button", { name: "Delete this profile?" }));
    await waitFor(() => expect(H.dels).toEqual(["/brain/profiles/bp1"]));
    expect(await screen.findByText("Deleted. It is no longer used by the app and 2 projects.")).toBeTruthy();
  });

  it("a 404 (an older Iron Jarvis) renders nothing", async () => {
    H.gets["/brain"] = new H.FakeApiError("Not Found", 404);
    const { container } = render(<BrainProfiles />);
    await waitFor(() => expect(H.getCalls).toContain("/brain"));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(container.innerHTML).toBe("");
  });
});

/* ======================================================================= */
const GROUPS = [
  { id: "models", label: "Models", description: "Which AI answers." },
  { id: "memory", label: "Memory & you", description: "Who you are." },
];
const BRAIN_ROW = {
  key: "brain_profile",
  label: "Brain profile",
  group: "memory",
  section: "",
  type: "string",
  aliases: ["primary memory base"],
};

describe("Settings home: the Brain profile row", () => {
  function withSchema(settings: unknown[]) {
    H.gets["/settings/schema"] = { groups: GROUPS, settings, secrets: [] };
    H.gets["/settings/values"] = { values: { brain_profile: "bp1" } };
    H.gets["/config/ledger"] = { changes: [] };
  }

  it("is a Memory & you section, drawn once (no plain schema row beside it)", async () => {
    expect(CONTROLLED_IN_SECTION.brain_profile).toBe("memory-brain-profile");
    withSchema([BRAIN_ROW]);
    window.history.replaceState({}, "", "/settings?section=memory");
    render(<SettingsHome />);
    const sub = await screen.findByTestId("settings-sub-memory-brain-profile", {}, W);
    expect(within(sub).getByText("Brain profile")).toBeTruthy();
    expect(document.getElementById("setting-brain_profile")).toBeNull();
    fireEvent.click(within(sub).getByRole("button", { expanded: false }));
    expect(await within(sub).findByTestId("brain-profiles", {}, W)).toBeTruthy();
  });

  it("a search for its words opens that section", async () => {
    withSchema([BRAIN_ROW]);
    render(<SettingsHome />);
    fireEvent.change(await screen.findByTestId("settings-search"), { target: { value: "primary memory base" } });
    expect(await screen.findByTestId("settings-sub-memory-brain-profile", {}, W)).toBeTruthy();
  });

  it("an older daemon whose schema lacks the key gets no section", async () => {
    withSchema([{ key: "x", label: "Something", group: "memory", section: "", type: "string" }]);
    window.history.replaceState({}, "", "/settings?section=memory");
    render(<SettingsHome />);
    await screen.findByTestId("settings-sub-memory-profile", {}, W);
    expect(screen.queryByTestId("settings-sub-memory-brain-profile")).toBeNull();
  });
});

/* ======================================================================= */
describe("the project page's select (component)", () => {
  it("offers the app's choice by name, none here, and each profile", async () => {
    const onChange = vi.fn();
    render(<ProjectBrainSelect value="" onChange={onChange} />);
    const sel = (await screen.findByLabelText("Brain profile", {}, W)) as HTMLSelectElement;
    expect(Array.from(sel.options).map((o) => [o.value, o.textContent])).toEqual([
      ["", "Same as the app (Notion)"],
      ["off", "None in this project"],
      ["bp1", "Notion"],
      ["bp2", "Obsidian vault"],
    ]);
    fireEvent.change(sel, { target: { value: "off" } });
    expect(onChange).toHaveBeenCalledWith("off");
  });

  it("no app choice reads (none); a deleted profile still shows", async () => {
    H.gets["/brain"] = { ...STATE, selected: "" };
    render(<ProjectBrainSelect value="gone" onChange={() => {}} />);
    const sel = (await screen.findByLabelText("Brain profile", {}, W)) as HTMLSelectElement;
    expect(sel.options[0].textContent).toBe("Same as the app (none)");
    expect(sel.value).toBe("gone");
  });

  it("404 renders nothing", async () => {
    H.gets["/brain"] = new H.FakeApiError("Not Found", 404);
    const { container } = render(<ProjectBrainSelect value="" onChange={() => {}} />);
    await waitFor(() => expect(H.getCalls).toContain("/brain"));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(container.innerHTML).toBe("");
  });

  it("the project page wires it to savePatch({brain_profile})", () => {
    const src = readSrc("app/projects/[id]/page.tsx");
    expect(src).toContain("<ProjectBrainSelect");
    expect(src).toContain('onChange={(v) => void savePatch({ brain_profile: v }, "brain")}');
  });
});

/* ======================================================================= */
const ROUTE = { requested: "", provider: "claude-cli", model: "opus", reason: "default" };
const SAVED = {
  name: "Notion",
  base: "Notion",
  searched: 3,
  guide: "loaded",
  saved: [{ title: "Q4 plan", ref: "notion://1", action: "created", verified: true }],
  unsaved: [],
  changed: [],
};

describe("the turn receipt", () => {
  it("a saved note rides the collapsed line in the accent, visible without expanding", () => {
    render(<TurnReceipt inline route={ROUTE} brain={decodeBrainReceipt(SAVED)} />);
    const line = screen.getByTestId("turn-brain");
    expect(line.textContent).toBe("Saved to Notion: Q4 plan");
    expect(line.getAttribute("data-tone")).toBe("accent");
    expect(line.className).toContain("text-accent-soft");
    expect(receiptWantsAttention({ route: ROUTE, brain: decodeBrainReceipt(SAVED) })).toBe(true);
  });

  it("an unsaved note is first, in the warn tone", () => {
    render(
      <TurnReceipt
        inline
        route={ROUTE}
        brain={decodeBrainReceipt({ ...SAVED, unsaved: [{ title: "Draft", reason: "Notion did not answer" }] })}
      />,
    );
    const line = screen.getByTestId("turn-brain");
    expect(line.textContent).toBe("Not saved yet: Draft. Notion did not answer.");
    expect(line.className).toContain("text-tone-warn");
  });

  it("an unconfirmed save says so", () => {
    render(
      <TurnReceipt
        route={ROUTE}
        brain={decodeBrainReceipt({ ...SAVED, saved: [{ title: "Q4 plan", action: "created", verified: false }] })}
      />,
    );
    expect(screen.getByTestId("turn-brain").textContent).toBe("Saved to Notion: Q4 plan · not confirmed");
  });

  it("the expanded receipt says the search and the guide", () => {
    render(<TurnReceipt inline route={ROUTE} brain={decodeBrainReceipt(SAVED)} />);
    fireEvent.click(screen.getByTestId("turn-receipt"));
    expect(screen.getAllByTestId("turn-brain-detail").map((d) => d.textContent)).toEqual([
      "Searched Notion first · 3 notes",
      "Operating guide loaded",
      "Saved in Notion: Q4 plan",
    ]);
  });

  it("a search alone is not on the collapsed line, and keeps the receipt quiet", () => {
    const quiet = decodeBrainReceipt({ ...SAVED, saved: [] });
    render(<TurnReceipt inline route={ROUTE} brain={quiet} />);
    expect(screen.queryByTestId("turn-brain")).toBeNull();
    expect(receiptWantsAttention({ route: ROUTE, brain: quiet })).toBe(false);
  });

  it("null says nothing, and alone renders nothing", () => {
    const { container } = render(<TurnReceipt brain={null} />);
    expect(container.innerHTML).toBe("");
    // A receipt fed raw disk data decodes it again.
    cleanup();
    render(<TurnReceipt route={ROUTE} brain={{ junk: true } as never} />);
    expect(screen.queryByTestId("turn-brain")).toBeNull();
  });
});

/* ======================================================================= */
describe("the chat drawer's line", () => {
  it("names the profile and links to the project page", async () => {
    H.gets["/brain/effective?project_id=p1"] = { profile: NOTION, source: "project" };
    render(<ChatBrainLine projectId="p1" />);
    const line = await screen.findByTestId("chat-brain-line", {}, W);
    expect(line.textContent).toBe("Brain profile: Notion (this project). Change it");
    expect(within(line).getByRole("link").getAttribute("href")).toBe("/projects/p1");
  });

  it("says nothing when none applies, and on 404", async () => {
    H.gets["/brain/effective?project_id=p1"] = { profile: null, source: "off" };
    render(<ChatBrainLine projectId="p1" />);
    await waitFor(() => expect(H.getCalls).toContain("/brain/effective?project_id=p1"));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(screen.queryByTestId("chat-brain-line")).toBeNull();
    cleanup();
    H.getCalls = [];
    H.gets["/brain/effective?project_id=p1"] = new H.FakeApiError("Not Found", 404);
    render(<ChatBrainLine projectId="p1" />);
    await waitFor(() => expect(H.getCalls).toContain("/brain/effective?project_id=p1"));
    expect(screen.queryByTestId("chat-brain-line")).toBeNull();
  });

  it("the chat page mounts it inside the project section", () => {
    const src = readSrc("app/chat/page.tsx");
    const at = src.indexOf("Replies use this project&apos;s instructions");
    const mount = src.indexOf("<ChatBrainLine projectId={activeProject.id} />");
    expect(at).toBeGreaterThan(0);
    expect(mount).toBeGreaterThan(at);
    expect(mount - at).toBeLessThan(800);
  });
});

/* ======================================================================= */
const PENDING = {
  pending: [
    {
      id: "u1",
      profile_name: "Notion",
      base: "Notion",
      title: "Q4 plan",
      preview: "Revenue target 1.2M",
      error: "Notion did not answer.",
      created_at: "2026-10-29T10:00:00Z",
    },
    { id: "u2", profile_name: "Notion", base: "Notion", title: "Draft", preview: "", error: "", created_at: "" },
  ],
};

describe("Memory › Unsaved changes", () => {
  it("lists the notes; Try again saves one and removes it", async () => {
    H.gets["/brain/pending"] = PENDING;
    H.answers["POST /brain/pending/u1/retry"] = { ok: true, saved: { ref: "notion://9", verified: true } };
    render(<BrainUnsaved />);
    const card = await screen.findByTestId("brain-unsaved", {}, W);
    expect(within(card).getByText("Notion did not answer.")).toBeTruthy();
    fireEvent.click(screen.getByTestId("brain-retry-u1"));
    await waitFor(() => expect(screen.queryByTestId("brain-pending-u1")).toBeNull());
    expect(H.posts[0].path).toBe("/brain/pending/u1/retry");
    expect(screen.getByTestId("brain-unsaved-note").textContent).toBe("Saved Q4 plan to Notion.");
  });

  it("a refused retry (409) shows its sentence on that row and keeps it", async () => {
    H.gets["/brain/pending"] = PENDING;
    H.answers["POST /brain/pending/u2/retry"] = new H.FakeApiError("Notion still is not reachable.", 409);
    render(<BrainUnsaved />);
    fireEvent.click(await screen.findByTestId("brain-retry-u2", {}, W));
    expect((await screen.findByTestId("brain-pending-error-u2")).textContent).toBe("Notion still is not reachable.");
    expect(screen.getByTestId("brain-pending-u2")).toBeTruthy();
  });

  it("an unconfirmed retry says so", async () => {
    H.gets["/brain/pending"] = PENDING;
    H.answers["POST /brain/pending/u1/retry"] = { ok: true, saved: { ref: "r", verified: false } };
    render(<BrainUnsaved />);
    fireEvent.click(await screen.findByTestId("brain-retry-u1", {}, W));
    expect((await screen.findByTestId("brain-unsaved-note")).textContent).toMatch(/did not confirm it yet/);
  });

  it("Discard takes two presses", async () => {
    H.gets["/brain/pending"] = PENDING;
    H.answers["DELETE /brain/pending/u2"] = { discarded: true };
    render(<BrainUnsaved />);
    const row = await screen.findByTestId("brain-pending-u2", {}, W);
    fireEvent.click(within(row).getByRole("button", { name: "Discard" }));
    expect(H.dels).toEqual([]);
    fireEvent.click(within(row).getByRole("button", { name: "Discard this note?" }));
    await waitFor(() => expect(screen.queryByTestId("brain-pending-u2")).toBeNull());
    expect(H.dels).toEqual(["/brain/pending/u2"]);
  });

  it("is hidden when empty and on 404; the Memory page mounts it", async () => {
    H.gets["/brain/pending"] = { pending: [] };
    const { container } = render(<BrainUnsaved />);
    await waitFor(() => expect(H.getCalls).toContain("/brain/pending"));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(container.innerHTML).toBe("");
    cleanup();
    H.gets["/brain/pending"] = new H.FakeApiError("Not Found", 404);
    H.getCalls = [];
    render(<MemoryPage />);
    await waitFor(() => expect(H.getCalls).toContain("/brain/pending"));
    expect(screen.queryByTestId("brain-unsaved")).toBeNull();
    cleanup();
    H.gets["/brain/pending"] = PENDING;
    render(<MemoryPage />);
    expect(await screen.findByTestId("brain-unsaved", {}, W)).toBeTruthy();
  });
});

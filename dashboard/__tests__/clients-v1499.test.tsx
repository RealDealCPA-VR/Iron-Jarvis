/**
 * v1.499.0 (vision plan leap 3, Phase 3): clients on the dashboard.
 *
 * A client is a person or business the owner works for, known by its
 * folders and confirmed by the owner. The daemon decides everything (whose
 * folder a folder is, what a chat is told); the dashboard shows what it was
 * handed and presses the client's own routes. These pins hold the screen to
 * that: the decoders drop unknown fields, rows with no client id and links
 * that leave the app; the review starts ticked only what was found under a
 * Clients folder and answers in one press; every claim on a client's page
 * names its record; "Not a client" takes two presses; a failed read is said;
 * the Memory page mounts the section in its place; a work-record folder
 * shows the server's own client; and "client" finds the Clients page.
 *
 * The routes are not running here: every answer is a mocked fetch in the
 * contract's wire shape (workmap/clients/__init__.py).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const H = vi.hoisted(() => ({
  answers: {} as Record<string, unknown>,
  gets: [] as string[],
  posts: [] as { path: string; body: unknown }[],
  patches: [] as { path: string; body: unknown }[],
  dels: [] as string[],
  postAnswer: {} as unknown,
  postError: null as null | { status: number; message: string },
  // A press that has not answered yet (the save still busy), when set.
  postGate: null as null | Promise<void>,
}));

vi.mock("@/lib/api", () => ({
  get: async (path: string) => {
    H.gets.push(path);
    const r = H.answers[path];
    if (r === undefined) throw { status: 404, message: "Not Found" };
    if (r && typeof r === "object" && "__fail" in (r as object)) throw (r as { __fail: unknown }).__fail;
    return JSON.parse(JSON.stringify(r));
  },
  post: async (path: string, body: unknown) => {
    H.posts.push({ path, body });
    if (H.postGate) await H.postGate;
    if (H.postError) throw H.postError;
    return H.postAnswer;
  },
  patch: async (path: string, body: unknown) => {
    H.patches.push({ path, body });
    return {};
  },
  del: async (path: string) => {
    H.dels.push(path);
    return {};
  },
}));
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

import type React from "react";
import {
  CLIENTS_ABOUT,
  CLIENT_ID_RE,
  MEMORY_EMPTY,
  NOT_ON_RECORD,
  POSSESSIVE_TABLE,
  THESE_ARE_CLIENTS,
  appLink,
  applySettledQuestions,
  chipWords,
  clientHref,
  clientVerbPath,
  decodeClientPage,
  decodeClientQuestion,
  decodeClientsList,
  mayMakeClient,
  possessive,
  questionLine,
  rememberClientAnswer,
  type ChatClientQuestion,
} from "@/lib/clients";
import * as CLIENT_WORDS from "@/lib/clients";
import { RECAP_STALE, decodeFolders } from "@/lib/workRecord";
import { ClientReview } from "@/components/clients/ClientReview";
import { ClientList } from "@/components/clients/ClientList";
import { ClientOfFolder, ClientPage } from "@/components/clients/ClientPage";
import { YourClients } from "@/components/memory/YourClients";
import { WorkByFolder } from "@/components/memory/WorkByFolder";
import { NAV_ENTRIES, NON_RAIL_ENTRIES, labelForPath } from "@/lib/nav";
import { SIDEBAR_HREFS, SURFACES, surfaceFor, ungrouped } from "@/lib/surfaces";
import { scorePalette, type PaletteItem } from "@/lib/palette";

const ROOT = join(__dirname, "..");
const src = (p: string) => readFileSync(join(ROOT, p), "utf8").replace(/\r\n/g, "\n");

// Made-up clients and made-up paths only.
const HID = "client_ab12cd34ef56"; // Hartleys
const LID = "client_0000000000a1"; // Lee (waiting, under a Clients folder)
const PRID = "client_0000000000b2"; // Price (waiting, seen in several folders)
const BID = "client_0000000000c3"; // Board Minutes (not a client, never was)
const SID = "client_0000000000d4"; // Smith (not a client now, was one)
const PREF1 = "cpref_aaaaaaaaaaa1";
const PREF2 = "cpref_aaaaaaaaaaa2";
const NOON = "2026-11-14T12:00:00Z";
const recent = () => new Date().toISOString();

const LIST = () => ({
  clients: [
    {
      id: HID,
      name: "Hartleys",
      how: "noticed",
      folders: [
        { folder: "C:\\Clients\\Hartleys", name: "Hartleys" },
        { folder: "D:\\Scans\\Hartleys", name: "Hartleys" },
      ],
      work_count: 9,
      last_at: recent(),
      preferences_count: 2,
      told: "should never be kept",
    },
  ],
  waiting: [
    {
      id: LID,
      name: "Lee",
      rule: "under_clients",
      about: "1 folder, 2 pieces of work",
      evidence: ["Clients › Lee: 2 pieces of work, last 3d ago"],
      folders: 1,
      work: 2,
      pre_ticked: true,
      created_at: NOON,
    },
    {
      id: PRID,
      name: "Price",
      rule: "several_folders",
      about: "2 folders, 3 pieces of work",
      evidence: ["Work › Price: 2 pieces of work", "Tax › Price: 1 piece of work"],
      folders: 2,
      work: 3,
      pre_ticked: false,
      created_at: NOON,
    },
  ],
  declined: [
    { id: BID, name: "Board Minutes", decided_at: NOON, was_client: false },
    { id: SID, name: "Smith", decided_at: NOON, was_client: true },
  ],
  total: 1,
  noticing: true,
});

const PAGE = () => ({
  client: {
    id: HID,
    name: "Hartleys",
    status: "confirmed",
    how: "noticed",
    aliases: ["Hartley's", "Hartley"],
    created_at: NOON,
    confirmed_at: NOON,
    why: ["Seen in Clients › Hartleys and Clients › Hartleys 2024, 9 pieces of work"],
    signature: "hartley",
  },
  folders: [
    { folder: "C:\\Clients\\Hartleys", name: "Hartleys", how: "found", count: 7, last_at: recent(), forgotten: false },
    { folder: "D:\\Scans\\Hartleys", name: "Hartleys", how: "added", count: 2, last_at: null, forgotten: false },
    { folder: "C:\\Clients\\Hartleys 2019", name: "Hartleys 2019", how: "found", count: 0, last_at: null, forgotten: true },
  ],
  entries: [
    {
      id: "chat:c1", kind: "chat", ref: "c1", title: "March reconciliation", tool: "", outcome: "",
      local_only: false, files: [], files_total: 0, note: "", updated_at: recent(), link: "/chat?thread=c1",
    },
  ],
  entries_total: 3,
  files: [
    {
      name: "recon_march.xlsx", path: "C:\\Clients\\Hartleys\\recon_march.xlsx", how: "wrote",
      folder_name: "Hartleys", entry_id: "chat:c1", title: "March reconciliation", link: "/chat?thread=c1", at: recent(),
    },
    // Names no record: never drawn.
    { name: "orphan.txt", path: "C:\\x\\orphan.txt", how: "made", folder_name: "x", entry_id: "", title: "", link: "" },
  ],
  files_total: 1,
  decisions: [
    {
      text: "Use cash basis", label: "decided", entry_id: "chat:c1", title: "March reconciliation",
      link: "/chat?thread=c1", written_at: recent(), current: true,
    },
    {
      text: "The 1,250.00 deposit is waiting on you", label: "open", entry_id: "chat:c2", title: "April questions",
      link: "/chat?thread=c2", written_at: recent(), current: false,
    },
    { text: "A line from nowhere", label: "decided", entry_id: "", title: "", link: "", written_at: null, current: true },
  ],
  people: [
    {
      name: "Mary Hartley", how: "title",
      seen_in: [{ entry_id: "chat:c3", title: "Call with Mary Hartley", link: "/chat?thread=c3" }],
    },
    { name: "Nobody Seen", how: "title", seen_in: [] },
  ],
  preferences: [
    {
      id: PREF1, text: "Use cash basis", origin: "said", local_only: false,
      evidence: [{ quote: "Hartleys wants cash basis", thread_id: "c1", where: "Hartleys 2026", at: NOON, link: "/chat?thread=c1" }],
      created_at: NOON,
    },
    { id: PREF2, text: "Send drafts to Mary first", origin: "typed", local_only: true, evidence: [], created_at: NOON },
    { id: "Hartleys", text: "A row with a name for an id", origin: "typed", local_only: false, evidence: [], created_at: NOON },
  ],
  named_elsewhere: [
    { entry_id: "chat:d1", title: "Hartleys letter from the bank", link: "/chat?thread=d1", folder_name: "Downloads" },
  ],
  projects: [
    { id: "p1", name: "Hartleys 2026 return", root: "C:\\Clients\\Hartleys\\2026" },
    { id: "../../settings", name: "Not a project id", root: "" },
  ],
  told:
    "# Client: Hartleys\nThis chat's folder belongs to Hartleys, a client the owner confirmed.\n- Use cash basis.",
});

beforeEach(() => {
  H.answers = {};
  H.gets = [];
  H.posts = [];
  H.patches = [];
  H.dels = [];
  H.postAnswer = {};
  H.postError = null;
  H.postGate = null;
});
afterEach(() => cleanup());

/* ===================================================================== words */

describe("the words", () => {
  it("one possessive table, pinned both ways", () => {
    // The literal rows are the contract with the daemon's names.possessive.
    expect(possessive("Hartley's")).toBe("Hartley's");
    expect(possessive("Hartleys")).toBe("Hartleys'");
    expect(possessive("Smith")).toBe("Smith's");
    for (const [name, want] of POSSESSIVE_TABLE) expect(possessive(name), name).toBe(want);
    expect(possessive("  Smith Dental ")).toBe("Smith Dental's");
    expect(possessive("Hartley\u2019s")).toBe("Hartley\u2019s");
  });

  it("the question under a reply is one sentence that counts in plain words", () => {
    expect(questionLine({ name: "Hartleys", folders: 2, work: 9 })).toBe(
      "Iron Jarvis has seen Hartleys in 2 folders and 9 pieces of work. Is Hartleys a client?",
    );
    expect(questionLine({ name: "Lee", folders: 1, work: 1 })).toBe(
      "Iron Jarvis has seen Lee in 1 folder and 1 piece of work. Is Lee a client?",
    );
    expect(questionLine({ name: "Lee", folders: 0, work: 0 })).toBe("Is Lee a client?");
  });

  it("never calls a client a customer or an account", () => {
    const said: string[] = [];
    for (const [key, v] of Object.entries(CLIENT_WORDS)) {
      if (typeof v === "string" && /^[A-Z_]+$/.test(key)) said.push(v);
      if (typeof v === "function" && v.length === 1 && !/^(decode|is|apply|map|may|client|press|app)/.test(key)) {
        try {
          const out = (v as (x: unknown) => unknown)("Hartleys");
          if (typeof out === "string") said.push(out);
        } catch {
          // A helper that takes a record, not a name: its words are read
          // through the components' own tests.
        }
      }
    }
    expect(said.length).toBeGreaterThan(30);
    for (const s of said) expect(s, s).not.toMatch(/\b(customers?|accounts?)\b/i);
  });

  it("the canon has the client row", () => {
    const canon = readFileSync(join(ROOT, "..", "VOCABULARY.md"), "utf8").replace(/\r\n/g, "\n");
    expect(canon).toMatch(
      /\| A person or business the owner works for, known by its folders and confirmed by the owner[^\n]*\| \*\*client\*\*;[^\n]*\| customer, account/,
    );
  });
});

/* ================================================================== decoders */

describe("the decoders", () => {
  it("the list keeps only what the page shows, and only rows with a client id", () => {
    const raw = LIST() as Record<string, unknown>;
    (raw.clients as unknown[]).push(
      { id: "Hartley's", name: "A name for an id" },
      { id: "client_AB12CD34EF56", name: "Upper case" },
      { id: "client_ab12cd34ef5", name: "Eleven" },
      { id: "client_0000000000e5", name: "   " },
      null,
      7,
    );
    (raw.waiting as unknown[]).push({ id: "../x", name: "Climbing out" });
    const got = decodeClientsList(raw)!;
    expect(got.clients).toEqual([
      {
        id: HID,
        name: "Hartleys",
        how: "noticed",
        folders: [
          { folder: "C:\\Clients\\Hartleys", name: "Hartleys" },
          { folder: "D:\\Scans\\Hartleys", name: "Hartleys" },
        ],
        workCount: 9,
        lastAt: got.clients[0].lastAt,
        preferencesCount: 2,
      },
    ]);
    expect(got.waiting.map((w) => [w.id, w.preTicked])).toEqual([
      [LID, true],
      [PRID, false],
    ]);
    expect(got.declined).toEqual([
      { id: BID, name: "Board Minutes", decidedAt: NOON, wasClient: false },
      { id: SID, name: "Smith", decidedAt: NOON, wasClient: true },
    ]);
    expect(got.noticing).toBe(true);
    expect(decodeClientsList({ ...LIST(), noticing: false })!.noticing).toBe(false);
    // Not a client list at all: nothing, never "no clients".
    for (const none of [null, undefined, {}, [], "x", { clients: "many" }]) expect(decodeClientsList(none)).toBeNull();
  });

  it("a link that leaves the app is dropped, and so is a claim that names no record", () => {
    const raw = PAGE();
    raw.decisions[0].link = "https://evil.example/x";
    raw.decisions[1].link = "//evil.example/x";
    raw.files[0].link = "/\\evil.example";
    raw.people[0].seen_in[0].link = "/chat\n?thread=c3";
    raw.named_elsewhere[0].link = "javascript:alert(1)";
    raw.preferences[0].evidence[0].link = "/chat?thread=c1";
    const got = decodeClientPage(raw)!;
    expect(got.decisions.map((d) => d.link)).toEqual(["", ""]);
    expect(got.files.map((f) => [f.name, f.link])).toEqual([["recon_march.xlsx", ""]]);
    expect(got.people.map((p) => [p.name, p.seenIn[0].link])).toEqual([["Mary Hartley", ""]]);
    expect(got.namedElsewhere[0].link).toBe("");
    expect(got.preferences[0].evidence[0].link).toBe("/chat?thread=c1");
    // A preference is changed by its own id only; a Project's link is built
    // from its id, never handed over.
    expect(got.preferences.map((p) => p.id)).toEqual([PREF1, PREF2]);
    expect(got.projects).toEqual([
      { id: "p1", name: "Hartleys 2026 return", root: "C:\\Clients\\Hartleys\\2026", href: "/projects/p1" },
    ]);
    expect("signature" in got.client).toBe(false);
    for (const bad of ["https://x.example/", "//x", "/a\\b", "/a\tb", "a/b", "", 7, null]) expect(appLink(bad)).toBe("");
    expect(appLink("/clients/x?y=1#z")).toBe("/clients/x?y=1#z");
    // No client, or a name where the id goes: no page.
    expect(decodeClientPage({ ...PAGE(), client: { ...PAGE().client, id: "Hartleys" } })).toBeNull();
    expect(decodeClientPage({ folders: [] })).toBeNull();
  });

  it("a client's page and its presses are built only from a client id", () => {
    expect(clientHref(HID)).toBe(`/clients/${HID}`);
    for (const bad of ["Hartley's", "../x", "client_AB12CD34EF56", `${HID}/decline`, "", 7, null]) {
      expect(clientHref(bad), String(bad)).toBe("/clients");
    }
    expect(clientVerbPath(HID, "confirm")).toBe(`/clients/${HID}/confirm`);
    expect(() => clientVerbPath("Hartleys", "confirm")).toThrow(NOT_ON_RECORD);
    expect(CLIENT_ID_RE.test("client_ab12cd34ef56")).toBe(true);
  });

  it("the question under a reply keeps its shape and its answer, and nothing else", () => {
    expect(decodeClientQuestion({ id: HID, name: "Hartleys", folders: 2, work: 9, path: "C:\\Clients" })).toEqual({
      id: HID,
      name: "Hartleys",
      folders: 2,
      work: 9,
    });
    expect(decodeClientQuestion({ id: HID, name: "Hartleys", folders: ["a", "b"], work: 9, state: "yes" })).toEqual({
      id: HID,
      name: "Hartleys",
      folders: 2,
      work: 9,
      state: "yes",
    });
    expect(decodeClientQuestion({ id: HID, name: "Hartleys", state: "maybe" })?.state).toBeUndefined();
    for (const bad of [null, {}, { id: "Hartleys", name: "Hartleys" }, { id: HID, name: "  " }, [HID]]) {
      expect(decodeClientQuestion(bad)).toBeNull();
    }
  });

  it("an answer given in this window is never put back by a later save", () => {
    const open: ChatClientQuestion = { id: HID, name: "Hartleys", folders: 2, work: 9 };
    const msgs = [{ role: "assistant", clientQuestion: open }, { role: "user" }];
    const settled = rememberClientAnswer(new Map(), open, { ...open, state: "yes" });
    expect(applySettledQuestions(msgs, settled)[0].clientQuestion?.state).toBe("yes");
    expect(applySettledQuestions(msgs, new Map())).toBe(msgs);
  });

  it("a remembered answer is written only onto the question it was given for, never a newer one for the same client", () => {
    // The owner said No in one chat; later "Ask again" on the Clients page
    // let Iron Jarvis ask once more, under a newer reply (same client id).
    const asked: ChatClientQuestion = { id: HID, name: "Hartleys", folders: 2, work: 9 };
    const settled = rememberClientAnswer(new Map(), asked, { ...asked, state: "no" });
    // The save of a turn that began before the press still holds the open ask.
    const stale = [{ role: "assistant", clientQuestion: asked }];
    expect(applySettledQuestions(stale, settled)[0].clientQuestion?.state).toBe("no");
    // The newer question, in this chat or any other chat of the window, stays open.
    const askedAgain: ChatClientQuestion = { id: HID, name: "Hartleys", folders: 2, work: 11 };
    const later = [{ role: "assistant", clientQuestion: asked }, { role: "user" }, { role: "assistant", clientQuestion: askedAgain }];
    const saved = applySettledQuestions(later, settled);
    expect(saved[0].clientQuestion?.state).toBe("no");
    expect(saved[2].clientQuestion).toBe(askedAgain);
    expect(saved[2].clientQuestion?.state).toBeUndefined();
    const otherChat = [{ role: "assistant", clientQuestion: { ...asked } }];
    expect(applySettledQuestions(otherChat, settled)).toBe(otherChat);
  });

  it("a work-record folder carries only the server's client, and only with a client id", () => {
    const got = decodeFolders({
      folders: [
        { folder: "C:\\Clients\\Hartleys", name: "Hartleys", count: 2, client: { id: HID, name: "Hartleys", x: 1 } },
        { folder: "C:\\Work\\Smith", name: "Smith", count: 1, client: null },
        { folder: "C:\\Odd", name: "Odd", count: 1, client: { id: "Hartleys", name: "Hartleys" } },
      ],
    }).folders;
    expect(got.map((f) => f.client ?? null)).toEqual([{ id: HID, name: "Hartleys" }, null, null]);
  });
});

/* =================================================================== review */

describe("the review", () => {
  const waiting = () => decodeClientsList(LIST())!.waiting;

  it("starts ticked only the names found under a Clients folder, and one press confirms the ticked ones", async () => {
    const changed = vi.fn();
    H.postAnswer = { confirmed: [LID], skipped: [] };
    render(<ClientReview waiting={waiting()} onChanged={changed} />);
    const rows = screen.getAllByTestId("client-waiting");
    expect(rows.map((r) => (within(r).getByTestId("client-tick") as HTMLInputElement).checked)).toEqual([true, false]);
    expect(within(rows[1]).getByTestId("client-waiting-evidence").textContent).toContain("Tax › Price");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: THESE_ARE_CLIENTS }));
    });
    expect(H.posts).toEqual([{ path: "/clients/confirm-many", body: { ids: [LID] } }]);
    await waitFor(() => expect(screen.getByTestId("client-review-note")).toHaveTextContent("1 name is a client now."));
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it("the owner's own ticks are what is sent, and a name left unticked is not turned down", async () => {
    H.postAnswer = { confirmed: [PRID], skipped: [{ id: LID, why: "Lee was already answered." }] };
    render(<ClientReview waiting={waiting()} />);
    const [lee, price] = screen.getAllByTestId("client-tick");
    fireEvent.click(lee);
    fireEvent.click(price);
    await act(async () => {
      fireEvent.click(screen.getByTestId("client-confirm-many"));
    });
    expect(H.posts).toEqual([{ path: "/clients/confirm-many", body: { ids: [PRID] } }]);
    await waitFor(() =>
      expect(screen.getByTestId("client-review-note")).toHaveTextContent("1 name is a client now. Lee: Lee was already answered."),
    );
    expect(H.posts.some((p) => p.path.endsWith("/decline"))).toBe(false);
  });

  it("with nothing ticked the press does nothing", () => {
    render(<ClientReview waiting={waiting().map((w) => ({ ...w, preTicked: false }))} />);
    const press = screen.getByTestId("client-confirm-many") as HTMLButtonElement;
    expect(press.disabled).toBe(true);
    fireEvent.click(press);
    expect(H.posts).toEqual([]);
  });

  it("Not a client on a row is that row's own decline", async () => {
    render(<ClientReview waiting={waiting()} />);
    await act(async () => {
      fireEvent.click(within(screen.getAllByTestId("client-waiting")[1]).getByTestId("client-waiting-decline"));
    });
    expect(H.posts).toEqual([{ path: `/clients/${PRID}/decline`, body: { expect: "proposed" } }]);
    await waitFor(() =>
      expect(screen.getByTestId("client-review-note")).toHaveTextContent("Iron Jarvis won't ask about Price again."),
    );
  });
});

/* ===================================================================== list */

describe("the Clients page", () => {
  it("lists the waiting names, the clients and the names that are not clients, each with its own press", async () => {
    H.answers["/clients"] = LIST();
    H.postAnswer = { ok: true, client: { id: SID, name: "Smith" }, note: "" };
    render(<ClientList />);
    const row = await screen.findByTestId("client-row");
    expect(within(row).getByRole("link", { name: "Hartleys" }).getAttribute("href")).toBe(`/clients/${HID}`);
    expect(row.textContent).toContain("2 folders · 9 pieces of work · last");
    expect(row.textContent).toContain("2 preferences");
    expect(screen.getAllByTestId("client-waiting")).toHaveLength(2);
    const declined = screen.getAllByTestId("client-declined");
    expect(declined.map((d) => within(d).getByTestId("client-bring-back").textContent)).toEqual([
      "Ask again",
      "Make it a client again",
    ]);
    await act(async () => {
      fireEvent.click(within(declined[1]).getByTestId("client-bring-back"));
    });
    expect(H.posts).toEqual([{ path: `/clients/${SID}/restore`, body: {} }]);
    await waitFor(() => expect(screen.getByTestId("clients-note")).toHaveTextContent("Smith is a client now."));
    await act(async () => {
      fireEvent.click(within(screen.getAllByTestId("client-declined")[0]).getByTestId("client-bring-back"));
    });
    expect(H.posts[1]).toEqual({ path: `/clients/${BID}/ask-again`, body: {} });
    // Every answer reads the list again; reading never changes anything.
    expect(H.gets.filter((g) => g === "/clients").length).toBeGreaterThanOrEqual(2);
  });

  it("Look again now and Add a client press their own routes", async () => {
    H.answers["/clients"] = LIST();
    render(<ClientList />);
    await screen.findByTestId("client-row");
    H.postAnswer = { noticed: 3 };
    await act(async () => {
      fireEvent.click(screen.getByTestId("client-look-again"));
    });
    expect(H.posts).toEqual([{ path: "/clients/notice", body: {} }]);
    await waitFor(() => expect(screen.getByTestId("clients-note")).toHaveTextContent("Looked again. 3 new names are waiting."));
    H.postAnswer = { ok: true, client: { id: "client_0000000000f6", name: "Smith Dental" }, note: "" };
    fireEvent.change(screen.getByTestId("client-add-folder"), { target: { value: "D:\\Clients\\Smith Dental" } });
    await act(async () => {
      fireEvent.click(screen.getByTestId("client-add-press"));
    });
    expect(H.posts[1]).toEqual({ path: "/clients", body: { folder: "D:\\Clients\\Smith Dental" } });
    await waitFor(() => expect(screen.getByTestId("clients-note")).toHaveTextContent("Smith Dental is a client now."));
  });

  it("a refusal is said in the route's own words", async () => {
    H.answers["/clients"] = LIST();
    render(<ClientList />);
    await screen.findByTestId("client-row");
    H.postError = { status: 400, message: "Downloads holds work for many people. Pick a folder that is only Hartleys'." };
    fireEvent.change(screen.getByTestId("client-add-folder"), { target: { value: "C:\\Users\\Me\\Downloads" } });
    await act(async () => {
      fireEvent.click(screen.getByTestId("client-add-press"));
    });
    await waitFor(() =>
      expect(screen.getByTestId("clients-note")).toHaveTextContent("Downloads holds work for many people."),
    );
  });

  it("a list that could not be read says so, never 'no clients'", async () => {
    H.answers["/clients"] = { __fail: { status: 503, message: "Your clients could not be read. Nothing was changed." } };
    render(<ClientList />);
    const line = await screen.findByTestId("clients-failed");
    expect(line.textContent).toContain("Your clients couldn't load");
    expect(screen.queryByTestId("clients-empty")).toBeNull();
  });

  it("the page's own title and about line", () => {
    const page = src("app/clients/page.tsx");
    expect(page).toContain('<PageHeader title="Clients"');
    expect(page).toContain("{CLIENTS_ABOUT}");
    expect(CLIENTS_ABOUT).toContain("tells a chat about a client only when the chat is in that client's folders");
    // Default export only (an App Router page file may not have another).
    for (const f of ["app/clients/page.tsx", "app/clients/[id]/page.tsx"]) {
      expect(src(f).match(/^export /gm), f).toEqual(["export "]);
      expect(src(f)).toMatch(/^export default function /m);
    }
  });
});

/* ===================================================================== page */

async function openPage(raw: unknown = PAGE()) {
  H.answers[`/clients/${HID}`] = raw;
  render(<ClientPage id={HID} />);
  return screen.findByTestId("client-page");
}

describe("a client's page", () => {
  it("every section says the record each line came from", async () => {
    await openPage();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Hartleys");
    expect(screen.getByTestId("client-since")).toHaveTextContent("A client since 14 Nov 2026 (you said yes)");
    expect(screen.getByTestId("client-aliases")).toHaveTextContent("Also written: Hartley's, Hartley");
    expect(screen.getByTestId("client-why")).toHaveTextContent(
      "Why Iron Jarvis asked:Seen in Clients › Hartleys and Clients › Hartleys 2024, 9 pieces of work",
    );
    // Folders: how each one joined.
    const how = screen.getAllByTestId("client-folder-how").map((p) => p.textContent);
    expect(how[0]).toMatch(/^Named Hartleys · 7 pieces of work, last /);
    expect(how[1]).toBe("Added by you · 2 pieces of work");
    expect(how[2]).toBe("Named Hartleys");
    expect(screen.getByTestId("client-folder-forgotten")).toHaveTextContent(
      "Forgotten: Iron Jarvis keeps no record here. It is still Hartleys' folder, so other clients' work stays out of it.",
    );
    // Preferences: where each was said or typed, and Local only.
    const prefs = within(screen.getByTestId("client-preferences"));
    expect(screen.getByRole("heading", { name: "What Hartleys' wants" })).toBeTruthy();
    expect(prefs.getAllByTestId("client-pref-why").map((p) => p.textContent)).toEqual([
      "Said in a chat in Hartleys 2026: “Hartleys wants cash basis”",
      "You typed this on 14 Nov",
    ]);
    expect(prefs.getAllByTestId("client-pref-local")).toHaveLength(1);
    expect(prefs.queryByText("A row with a name for an id")).toBeNull();
    // What a chat is told: the daemon's own words.
    expect(screen.getByRole("heading", { name: "What a chat in Hartleys' folders is told" })).toBeTruthy();
    expect(screen.getByTestId("client-told-text").textContent).toBe(PAGE().told);
    // Decided and still open: the recap each came from.
    const decisions = screen.getAllByTestId("client-decision");
    expect(decisions).toHaveLength(2);
    expect(within(decisions[0]).getByRole("link").textContent).toMatch(/^From the recap of “March reconciliation”/);
    expect(within(decisions[0]).getByRole("link").getAttribute("href")).toBe("/chat?thread=c1");
    expect(screen.queryByText("A line from nowhere")).toBeNull();
    // Work, files and people: the work each was seen in.
    expect(within(screen.getByTestId("client-work")).getByRole("link", { name: "March reconciliation" })).toBeTruthy();
    expect(screen.getByTestId("client-work").textContent).toContain("and 2 older");
    const file = screen.getByTestId("client-file");
    expect(file.textContent).toContain("recon_march.xlsx · Hartleys · wrote");
    expect(within(file).getByRole("link", { name: "March reconciliation" }).getAttribute("href")).toBe("/chat?thread=c1");
    expect(screen.queryByText("orphan.txt")).toBeNull();
    const person = screen.getByTestId("client-person");
    expect(person.textContent).toContain("Mary Hartley · Named in: Call with Mary Hartley");
    expect(screen.queryByText("Nobody Seen")).toBeNull();
    expect(screen.getByTestId("client-named-elsewhere").textContent).toContain("Hartleys letter from the bank · Downloads");
    expect(within(screen.getByTestId("client-projects")).getByRole("link").getAttribute("href")).toBe("/projects/p1");
  });

  it("a decision from a recap written before the latest messages is shown stale", async () => {
    await openPage();
    const [decided] = within(screen.getByTestId("client-decided")).getAllByTestId("client-decision");
    const [open] = within(screen.getByTestId("client-open")).getAllByTestId("client-decision");
    expect(within(decided).queryByTestId("client-decision-stale")).toBeNull();
    expect(within(open).getByTestId("client-decision-stale")).toHaveTextContent(RECAP_STALE);
  });

  it("Not a client takes two presses", async () => {
    await openPage();
    const press = screen.getByTestId("client-not-a-client");
    expect(press).toHaveTextContent("Not a client");
    fireEvent.click(press);
    expect(H.posts).toEqual([]);
    expect(screen.getByTestId("client-not-a-client")).toHaveTextContent("Not a client?");
    await act(async () => {
      fireEvent.click(screen.getByTestId("client-not-a-client"));
    });
    // The two-press footer is the one press that may take a CONFIRMED client away.
    expect(H.posts).toEqual([{ path: `/clients/${HID}/decline`, body: { expect: "confirmed" } }]);
    await waitFor(() => expect(screen.getByTestId("client-note")).toHaveTextContent("Iron Jarvis won't ask about Hartleys again."));
  });

  it("a preference is kept, changed and forgotten through the client's own routes", async () => {
    await openPage();
    fireEvent.change(screen.getByTestId("client-pref-add-input"), { target: { value: "Uses cash basis" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Keep for Hartleys" }));
    });
    expect(H.posts).toEqual([{ path: `/clients/${HID}/preferences`, body: { text: "Uses cash basis" } }]);
    const forget = screen.getAllByTestId("client-pref-forget")[0];
    fireEvent.click(forget);
    expect(H.dels).toEqual([]);
    await act(async () => {
      fireEvent.click(screen.getAllByTestId("client-pref-forget")[0]);
    });
    expect(H.dels).toEqual([`/clients/${HID}/preferences/${PREF1}`]);
  });

  it("a waiting name's page asks the question, and a page that cannot be read says so", async () => {
    await openPage({ ...PAGE(), client: { ...PAGE().client, status: "proposed", confirmed_at: null } });
    expect(screen.getByTestId("client-ask")).toHaveTextContent("Is Hartleys a client?");
    expect(screen.queryByTestId("client-not-a-client")).toBeNull();
    await act(async () => {
      fireEvent.click(screen.getByTestId("client-ask-confirm"));
    });
    expect(H.posts).toEqual([{ path: `/clients/${HID}/confirm`, body: {} }]);
    cleanup();
    H.answers[`/clients/${HID}`] = { __fail: { status: 503, message: "x" } };
    render(<ClientPage id={HID} />);
    expect((await screen.findByTestId("client-failed")).textContent).toContain("This client's page couldn't load");
  });

  it("a 404 says the client is not on record, and an id that is not a client id reads nothing", async () => {
    render(<ClientPage id={HID} />);
    expect(await screen.findByTestId("client-missing")).toHaveTextContent(NOT_ON_RECORD);
    cleanup();
    H.gets = [];
    render(<ClientPage id="Hartley's" />);
    expect(screen.getByTestId("client-missing")).toHaveTextContent(NOT_ON_RECORD);
    render(<ClientPage id="..%2F..%2Fsettings" />);
    await act(async () => {});
    expect(H.gets).toEqual([]);
  });
});

/* =================================================================== memory */

describe("the Memory page", () => {
  it("mounts Your clients between what it knows about you and your work by folder", () => {
    const s = src("components/memory/MemorySurface.tsx");
    const knows = s.indexOf("<KnowsAboutYou");
    const clients = s.indexOf("<YourClients />");
    const work = s.indexOf("<WorkByFolder"); // 13-b: it takes aboveSettled
    expect(knows).toBeGreaterThan(-1);
    expect(clients).toBeGreaterThan(knows);
    expect(work).toBeGreaterThan(clients);
  });

  it("says how many names wait, with the way to them, then the clients", async () => {
    H.answers["/clients"] = LIST();
    render(<YourClients />);
    const waiting = await screen.findByTestId("your-clients-waiting");
    expect(waiting.textContent).toBe("Iron Jarvis noticed 2 names in your folders that look like clients. Look at them");
    expect(within(waiting).getByRole("link").getAttribute("href")).toBe("/clients#waiting");
    const row = screen.getByTestId("your-client");
    expect(within(row).getByRole("link", { name: "Hartleys" }).getAttribute("href")).toBe(`/clients/${HID}`);
  });

  it("with nothing on record it shows the way, in one line", async () => {
    H.answers["/clients"] = { clients: [], waiting: [], declined: [], total: 0, noticing: true };
    render(<YourClients />);
    expect((await screen.findByTestId("your-clients-empty")).textContent).toBe(MEMORY_EMPTY);
  });

  it("a 503 or an answer that is not a client list is said, and a 404 draws nothing", async () => {
    H.answers["/clients"] = { __fail: { status: 503, message: "x" } };
    render(<YourClients />);
    expect((await screen.findByTestId("your-clients-failed")).textContent).toContain("Your clients couldn't load");
    cleanup();
    delete H.answers["/clients"];
    render(<YourClients />);
    await waitFor(() => expect(H.gets.length).toBe(2));
    await waitFor(() => expect(screen.queryByTestId("your-clients-memory")).toBeNull());
    cleanup();
    // v1.499.1: an odd answer (a daemon mid-restart) is a failed read with
    // Try again, never a section that is gone until the page is reloaded.
    H.answers["/clients"] = {};
    render(<YourClients />);
    expect((await screen.findByTestId("your-clients-failed")).textContent).toContain("Your clients couldn't load");
  });
});

/* ============================================================ work by folder */

describe("Your work, by folder", () => {
  const FOLDERS = () => ({
    folders: [
      { folder: "C:\\Clients\\Hartleys", name: "Hartleys", count: 2, last_at: null, client: { id: HID, name: "Hartleys" } },
      { folder: "C:\\Work\\Smith", name: "Smith", count: 1, last_at: null, client: null },
      { folder: "C:\\Odd\\Place", name: "Place", count: 1, last_at: null, client: { id: "Hartleys", name: "Hartleys" } },
      { folder: "C:\\Clients", name: "Clients", count: 1, last_at: null, client: null },
      { folder: "C:\\Users\\Me\\Downloads", name: "Downloads", count: 4, last_at: null, client: null },
    ],
    total: 9,
    building: false,
  });

  it("a folder shows the server's own client, and a folder with none offers one press", async () => {
    H.answers["/work-record/folders"] = FOLDERS();
    render(<WorkByFolder />);
    const rows = await screen.findAllByTestId("work-folder");
    const chip = within(rows[0]).getByTestId("work-folder-client");
    expect(chip.textContent).toBe(chipWords("Hartleys"));
    expect(chip.getAttribute("href")).toBe(`/clients/${HID}`);
    expect(within(rows[0]).queryByTestId("work-folder-make-client")).toBeNull();
    // A client the server did not name with a client id is no client at all.
    expect(within(rows[2]).queryByTestId("work-folder-client")).toBeNull();
    // Too wide to be one client's: no press.
    expect(within(rows[3]).queryByTestId("work-folder-make-client")).toBeNull();
    expect(within(rows[4]).queryByTestId("work-folder-make-client")).toBeNull();
    // The folder row's first button still opens the folder.
    expect(within(rows[1]).getAllByRole("button")[0].getAttribute("aria-expanded")).toBe("false");

    H.postAnswer = { ok: true, client: { id: SID, name: "Smith" }, note: "" };
    const reads = H.gets.length;
    await act(async () => {
      fireEvent.click(within(rows[1]).getByTestId("work-folder-make-client"));
    });
    expect(H.posts).toEqual([{ path: "/clients", body: { folder: "C:\\Work\\Smith" } }]);
    await waitFor(() => expect(screen.getByTestId("work-forget-note")).toHaveTextContent("Smith is a client now."));
    await waitFor(() => expect(H.gets.length).toBeGreaterThan(reads));
  });

  it("a folder the rules refuse says why, in the route's own words", async () => {
    H.answers["/work-record/folders"] = FOLDERS();
    render(<WorkByFolder />);
    const rows = await screen.findAllByTestId("work-folder");
    H.postError = { status: 400, message: "C:\\Work holds Smith's folder. Pick Hartleys' own folder." };
    await act(async () => {
      fireEvent.click(within(rows[2]).getByTestId("work-folder-make-client"));
    });
    await waitFor(() => expect(screen.getByTestId("work-forget-note")).toHaveTextContent("holds Smith's folder"));
  });

  it("which folders may offer the press", () => {
    for (const [folder, want] of [
      ["C:\\Work\\Smith", true],
      ["D:\\Clients\\Hartleys 2026", true],
      ["\\\\server\\share\\Hartleys", true],
      ["C:\\", false],
      ["C:", false],
      ["\\\\server\\share", false],
      ["C:\\Clients", false],
      ["D:\\Tax Clients", false],
      ["C:\\Users\\Me\\Downloads", false],
      ["C:\\Users\\Me\\Desktop", false],
      ["", false],
    ] as const) {
      expect(mayMakeClient(folder), folder).toBe(want);
    }
  });
});

/* ====================================================== where the page lives */

describe("where Clients lives", () => {
  it("'client' and 'customer' find Clients, not Projects; it is not a sidebar row", () => {
    const entry = NON_RAIL_ENTRIES.find((e) => e.href === "/clients");
    expect(entry?.label).toBe("Clients");
    expect(entry?.aliases).toEqual(expect.arrayContaining(["client", "customer"]));
    expect((entry?.blurb ?? "").length).toBeLessThanOrEqual(120);
    expect(NAV_ENTRIES.find((e) => e.href === "/projects")?.aliases).not.toContain("client");
    expect(NAV_ENTRIES.find((e) => e.href === "/projects")?.aliases).toEqual(expect.arrayContaining(["matter", "case", "workspace"]));
    expect(surfaceFor("/clients")?.group).toBe("knowledge");
    expect(ungrouped()).toEqual([]);
    expect(SIDEBAR_HREFS as readonly string[]).not.toContain("/clients");
    expect(labelForPath("/clients")).toBe("Clients");
    expect(labelForPath(`/clients/${HID}`)).toBe("Clients");
    const items: PaletteItem[] = SURFACES.map((s) => ({
      id: `page:${s.href}`,
      kind: "page",
      label: s.label,
      blurb: s.blurb,
      aliases: s.aliases,
      href: s.href,
    }));
    for (const q of ["client", "customer", "my clients"]) {
      const found = scorePalette(q, items).map((i) => i.href);
      expect(found[0], q).toBe("/clients");
      expect(found, q).not.toContain("/projects");
    }
  });

  it("the Project page names its folder's client through the server's answer", async () => {
    const path = `/clients/of?folder=${encodeURIComponent("C:\\Clients\\Hartleys\\2026")}`;
    H.answers[path] = { client: { id: HID, name: "Hartleys" } };
    render(<ClientOfFolder folder={"C:\\Clients\\Hartleys\\2026"} />);
    const chip = await screen.findByTestId("client-of-folder");
    expect(chip.textContent).toBe("Client: Hartleys");
    expect(chip.getAttribute("href")).toBe(`/clients/${HID}`);
    expect(H.gets).toEqual([path]);
    cleanup();
    H.answers[path] = { client: null };
    render(<ClientOfFolder folder={"C:\\Clients\\Hartleys\\2026"} />);
    await waitFor(() => expect(H.gets).toHaveLength(2));
    expect(screen.queryByTestId("client-of-folder")).toBeNull();
    // Wired in where the Project shows its folder.
    expect(src("app/projects/[id]/page.tsx")).toContain("{project.root && <ClientOfFolder folder={project.root} />}");
  });
});

/* ===================================================== the independent review */

describe("the independent review", () => {
  it("Look again now is off while noticing is off", async () => {
    H.answers["/clients"] = { ...LIST(), noticing: false };
    render(<ClientList />);
    await screen.findByTestId("client-row");
    expect((screen.getByTestId("client-look-again") as HTMLButtonElement).disabled).toBe(true);
    cleanup();
    H.answers["/clients"] = LIST();
    render(<ClientList />);
    await screen.findByTestId("client-row");
    expect((screen.getByTestId("client-look-again") as HTMLButtonElement).disabled).toBe(false);
  });

  it("a confirmed client with no folder is never said to be 'not a client now'", async () => {
    await openPage({ ...PAGE(), folders: [], told: "", told_for: "" });
    expect(screen.getByTestId("client-told-nothing").textContent).toBe(
      "Hartleys has no folder yet, so no chat is told about Hartleys.",
    );
    cleanup();
    await openPage({ ...PAGE(), client: { ...PAGE().client, status: "declined" }, told: "" });
    expect(screen.getByTestId("client-told-nothing").textContent).toBe("Nothing is told: it is not a client now.");
  });

  it("the preview of what is told names the folder it was built for", async () => {
    await openPage({ ...PAGE(), told_for: "C:\\Clients\\Hartleys" });
    expect(screen.getByTestId("client-told-for").textContent).toBe("A chat in C:\\Clients\\Hartleys is told:");
  });

  it("a folder row the server says names nobody offers no Make this a client", async () => {
    H.answers["/work-record/folders"] = {
      folders: [
        { folder: "C:\\Clients\\Hartleys\\2026", name: "2026", count: 1, last_at: null, client: null, may_make_client: false },
        { folder: "C:\\Work\\Pereira Farms", name: "Pereira Farms", count: 1, last_at: null, client: null, may_make_client: true },
      ],
      total: 2,
      building: false,
    };
    render(<WorkByFolder />);
    const rows = await screen.findAllByTestId("work-folder");
    expect(within(rows[0]).queryByTestId("work-folder-make-client")).toBeNull();
    expect(within(rows[1]).getByTestId("work-folder-make-client")).toBeTruthy();
  });

  it("each one-press answer names its row for a screen reader, and the chat answer is announced", async () => {
    render(<ClientReview waiting={decodeClientsList(LIST())!.waiting} />);
    const declines = screen.getAllByTestId("client-waiting-decline");
    const names = decodeClientsList(LIST())!.waiting.map((w) => w.name);
    expect(declines.map((b) => b.getAttribute("aria-label"))).toEqual(names.map((n) => `Not a client: ${n}`));
    cleanup();
    H.answers["/clients"] = LIST();
    render(<ClientList />);
    await screen.findByTestId("client-row");
    const back = screen.getAllByTestId("client-bring-back").map((b) => b.getAttribute("aria-label"));
    expect(back.every((l) => /^(Ask again|Make it a client again): \S/.test(l ?? ""))).toBe(true);
  });
});

describe("the second review", () => {
  it("Remove, Forget and Not a client cannot be pressed while another save is busy", async () => {
    await openPage();
    let answer: () => void = () => {};
    H.postGate = new Promise<void>((r) => {
      answer = r;
    });
    fireEvent.change(screen.getByTestId("client-pref-add-input"), { target: { value: "Uses cash basis" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Keep for Hartleys" }));
    });
    // The keep is still being saved: a confirmed press now would be dropped.
    const waiting = [
      ...screen.getAllByTestId("client-folder-remove"),
      ...screen.getAllByTestId("client-pref-forget"),
      screen.getByTestId("client-not-a-client"),
    ];
    expect(waiting.length).toBeGreaterThan(2);
    for (const b of waiting) expect(b).toBeDisabled();
    await act(async () => {
      answer();
    });
    await waitFor(() => expect(screen.getByTestId("client-not-a-client")).not.toBeDisabled());
    for (const b of screen.getAllByTestId("client-folder-remove")) expect(b).not.toBeDisabled();
    for (const b of screen.getAllByTestId("client-pref-forget")) expect(b).not.toBeDisabled();
  });

  it("an answer that is not a client list is said as a failed read, and Try again brings the section back", async () => {
    H.answers["/clients"] = { detail: "starting" };
    render(<YourClients />);
    const failed = await screen.findByTestId("your-clients-failed");
    expect(failed.textContent).toContain("Your clients couldn't load");
    H.answers["/clients"] = LIST();
    await act(async () => {
      fireEvent.click(within(failed).getByRole("button"));
    });
    expect(await screen.findByTestId("your-client")).toHaveTextContent("Hartleys");
    expect(screen.queryByTestId("your-clients-failed")).toBeNull();
  });
});

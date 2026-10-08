/**
 * v1.315.0 — UX & aesthetic wave 3 ("the surfaces people use most"), track
 * T4 (build & files): Creative, Documents and File search.
 *
 * WHAT THE USER SAW (fresh/desk/phone__creative, __documents, __filesearch):
 *  - Creative: a small lowercase checkbox "also get a public URL" beside
 *    Upload media. Ticked once, every later pick OR drag-and-drop published
 *    the file to Pixio's PUBLIC CDN — permanently — with no prompt, while the
 *    lightbox and the tile popover both go through confirmPublish(). The one
 *    action in Creative that cannot be taken back was the one that never asked.
 *  - Creative: the image lightbox is a hand-rolled `fixed inset-0` overlay
 *    inside the page tree, not the shared <Modal> (portal + focus in/trap/back).
 *  - Documents: "Saved C:\…\summary.docx" and "Redacted N items → <path>" —
 *    a path and nothing to do with it. No Open, no Copy path.
 *  - File search: "Project (default)" searches an install-level folder, none
 *    of the user's projects are offered, the modes read "content / name /
 *    semantic", and the folder placeholder shows DOUBLED backslashes.
 *
 * Anti-vacuity (every describe): an unticked upload still uploads without a
 * prompt; confirmPublish's sentence is unchanged; the lightbox keeps Escape,
 * arrow-key navigation and focus return; Documents keeps the saved path, the
 * byte count and the scan-before-write redaction flow; File search keeps the
 * daemon default (no root sent), the drives, the custom path (which wins) and
 * the Browse… picker.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const hooks = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    FakeApiError,
    responses: {} as Record<string, unknown>,
    gets: [] as string[],
    posts: [] as { path: string; body?: unknown }[],
    /** Per-path POST answers: a value, a FakeApiError, or a function of the body. */
    postAnswers: {} as Record<string, unknown>,
  };
});

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (hooks.responses[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? (hooks.responses[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));
vi.mock("@/lib/api", () => ({
  API_BASE: "http://test",
  ApiError: hooks.FakeApiError,
  ijToken: () => "",
  setIjToken: () => {},
  onUnauthorizedChange: () => () => {},
  wsUrl: (p: string) => `ws://test${p}`,
  sseUrl: (p: string) => `http://test${p}`,
  get: (path: string) => {
    hooks.gets.push(path);
    if (path.startsWith("/filesearch?")) return Promise.resolve({ results: [] });
    const r = hooks.responses[path];
    return r === undefined
      ? Promise.reject(new hooks.FakeApiError(`unmocked GET ${path}`, 404))
      : Promise.resolve(r);
  },
  post: (path: string, body?: unknown) => {
    hooks.posts.push({ path, body });
    const a = hooks.postAnswers[path];
    if (a instanceof hooks.FakeApiError) return Promise.reject(a);
    if (typeof a === "function") return Promise.resolve((a as (b: unknown) => unknown)(body));
    return Promise.resolve(a ?? {});
  },
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
  api: () => Promise.resolve({}),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/",
}));
vi.mock("@/components/VoiceInput", () => ({
  VoiceInput: () => null,
  appendDictation: (p: string, c: string) => p + c,
}));
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
// Renders the header ACTIONS too: Creative's publish checkbox lives there.
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title, subtitle, actions }: { title: string; subtitle?: React.ReactNode; actions?: React.ReactNode }) => (
    <div>
      <h1>{title}</h1>
      <p data-testid="page-subtitle">{subtitle}</p>
      {actions}
    </div>
  ),
}));
vi.mock("@/components/FilePickerModal", () => ({
  FilePickerModal: ({ open, title }: { open: boolean; title?: string }) =>
    open ? <div data-testid="picker">{title}</div> : null,
}));
vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set([
    "initial", "animate", "exit", "transition", "variants", "layout",
    "whileHover", "whileTap", "whileInView", "viewport",
  ]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  const cache = new Map<string, unknown>();
  return {
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: React.ReactNode }) => createElement(Fragment, null, children),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => {
        const key = String(tag);
        if (!cache.has(key)) cache.set(key, tagFor(key));
        return cache.get(key);
      },
    }),
  };
});

const DocumentsPage = (await import("@/app/documents/page")).default;
const CreativePage = (await import("@/app/creative/page")).default;
const FileSearchPage = (await import("@/app/filesearch/page")).default;

const src = (rel: string): string => readFileSync(join(process.cwd(), rel), "utf8").replace(/\r\n/g, "\n");

beforeEach(() => {
  hooks.posts = [];
  hooks.gets = [];
  hooks.postAnswers = {};
  hooks.responses = { "/documents/live": { docs: [] }, "/helpdocs": { docs: [] } };
  window.localStorage.clear();
  window.HTMLElement.prototype.scrollTo = vi.fn();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/* ========================================================================== */
/*  Creative — "also publish" goes through THE consent gate                    */
/* ========================================================================== */

const ITEMS_PATH = "/creative/items?limit=500";
// confirmPublish()'s sentence, verbatim. creative-consent-relaunch-v1192 pins
// its behaviour; the upload path must reuse it, never write a second prompt.
const CONSENT =
  "Publish this file to Pixio's public CDN? Anyone with the link can view it, and the URL is permanent.";

const uploads = () => hooks.posts.filter((p) => p.path === "/creative/upload");

function renderCreative(items: unknown[] = []) {
  hooks.responses[ITEMS_PATH] = { items, count: items.length };
  hooks.postAnswers["/creative/upload"] = (body: unknown) => {
    const b = body as { filename: string; publish?: boolean };
    return { name: b.filename, size: 3, ...(b.publish ? { url: `https://cdn.test/${b.filename}` } : {}) };
  };
  return render(<CreativePage />);
}

const png = (name: string) => new File(["abc"], name, { type: "image/png" });

function publishBox(): HTMLInputElement {
  return screen.getByRole("checkbox", { name: /public/i }) as HTMLInputElement;
}

function pickFiles(container: HTMLElement, files: File[]) {
  const input = container.querySelector('input[type="file"][accept^="image"]') as HTMLInputElement | null;
  expect(input).not.toBeNull();
  fireEvent.change(input as HTMLInputElement, { target: { files } });
}

function dropFiles(files: File[]) {
  const ev = new Event("drop", { bubbles: true, cancelable: true });
  Object.defineProperty(ev, "dataTransfer", { value: { types: ["Files"], files } });
  act(() => {
    window.dispatchEvent(ev);
  });
}

describe("Creative: 'also publish' asks once per batch, before anything is sent", () => {
  it("declined: uploads WITHOUT publishing and says so; asked once, before the first POST", async () => {
    const askedAt: number[] = [];
    const confirmSpy = vi.spyOn(window, "confirm").mockImplementation(() => {
      askedAt.push(uploads().length);
      return false;
    });
    const { container } = renderCreative();
    fireEvent.click(publishBox());
    pickFiles(container, [png("a.png"), png("b.png")]);

    // Wait on the END of the handler (the success note), not on the POST.
    await waitFor(() => expect(screen.getByText(/Uploaded 2 files/)).toBeInTheDocument());
    expect(confirmSpy).toHaveBeenCalledTimes(1); // once per BATCH, not per file
    expect(confirmSpy.mock.calls[0][0]).toBe(CONSENT); // THE gate's own sentence
    expect(askedAt).toEqual([0]); // asked before any upload left the page
    expect(uploads()).toHaveLength(2);
    for (const u of uploads()) expect((u.body as Record<string, unknown>).publish).toBeUndefined();
    // Honest about the outcome: the user is told nothing was published.
    expect(screen.getByText(/Not published/i).textContent ?? "").toMatch(/declined/i);
    expect(screen.queryByText(/cdn\.test/)).toBeNull();
  });

  it("accepted: every file in the batch publishes, and the prompt still ran exactly once", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { container } = renderCreative();
    fireEvent.click(publishBox());
    pickFiles(container, [png("a.png"), png("b.png")]);

    await waitFor(() => expect(screen.getByText(/Uploaded 2 files/)).toBeInTheDocument());
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(confirmSpy.mock.calls[0][0]).toBe(CONSENT);
    expect(uploads().map((u) => (u.body as Record<string, unknown>).publish)).toEqual([true, true]);
    expect(screen.queryByText(/Not published/i)).toBeNull();
  });

  it("drag-and-drop goes through the same gate (declined -> no publish)", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderCreative();
    fireEvent.click(publishBox());
    dropFiles([png("drop.png")]);

    await waitFor(() => expect(screen.getByText(/Uploaded drop\.png/)).toBeInTheDocument());
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(uploads()).toHaveLength(1);
    expect((uploads()[0].body as Record<string, unknown>).publish).toBeUndefined();
  });

  it("anti-vacuity: unticked, an upload is one action with no prompt and no publish", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { container } = renderCreative();
    expect(publishBox().checked).toBe(false);
    pickFiles(container, [png("plain.png")]);

    await waitFor(() => expect(screen.getByText(/Uploaded plain\.png/)).toBeInTheDocument());
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(uploads()).toHaveLength(1);
    expect((uploads()[0].body as Record<string, unknown>).publish).toBeUndefined();
    expect((uploads()[0].body as Record<string, unknown>).filename).toBe("plain.png");
    expect(screen.queryByText(/Not published/i)).toBeNull();
  });

  it("the checkbox says where the file goes and that the link is permanent (sentence case)", () => {
    renderCreative();
    const label = (publishBox().closest("label")?.textContent ?? "").trim();
    expect(label).toMatch(/public/i);
    expect(label).toMatch(/permanent/i);
    expect(label.charAt(0)).toBe(label.charAt(0).toUpperCase());
    expect(label.charAt(0)).not.toBe(label.charAt(0).toLowerCase()); // a letter, upper case
  });

  it("source: uploadFiles calls confirmPublish(); confirmPublish's sentence is unchanged", () => {
    const s = src("app/creative/page.tsx");
    const start = s.indexOf("const uploadFiles = useCallback(");
    const end = s.indexOf("const onFile", start);
    expect(start).toBeGreaterThan(0);
    expect(end).toBeGreaterThan(start);
    expect(s.slice(start, end)).toMatch(/confirmPublish\(\)/);
    // Anti-vacuity: ONE gate, its words untouched.
    expect(s.match(/function confirmPublish\(\)/g)).toHaveLength(1);
    expect(s).toContain(`"${CONSENT}"`);
  });
});

/* ========================================================================== */
/*  Creative — the lightbox is the shared <Modal>                              */
/* ========================================================================== */

const ITEM = (name: string, created: string) => ({
  name,
  version: 1,
  media: "image" as const,
  kind: "image",
  filename: `${name}.png`,
  size: 1234,
  session_id: null,
  created_at: created,
  url: `/creative/file/${name}`,
});

// Mirrors the Modal primitive's tabbable rule (attributes, never layout).
const TABBABLE =
  'a[href], button, input, select, textarea, summary, [tabindex]:not([tabindex="-1"])';
function tabbables(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(TABBABLE)).filter(
    (el) => !el.hasAttribute("disabled") && !el.closest("[hidden]") && !(el instanceof HTMLInputElement && el.type === "hidden"),
  );
}

async function openLightbox(items = [ITEM("sunset", "2026-08-20T10:00:00")]) {
  const view = renderCreative(items);
  const tile = (await screen.findByText(`${items[0].filename}`)).closest('[role="button"]') as HTMLElement;
  expect(tile).not.toBeNull();
  tile.focus();
  fireEvent.click(tile);
  const dialog = await screen.findByRole("dialog");
  return { ...view, tile, dialog };
}

describe("Creative: the lightbox is a real dialog (Modal: portal, focus in/trap/back)", () => {
  it("renders through the shared portal — outside the page tree — as ONE dialog, same size as before", async () => {
    const { container } = await openLightbox();
    const dialog = await screen.findByRole("dialog", { name: "sunset.png" });
    // <Modal> portals into document.body; the hand-rolled overlay rendered
    // inside the page (and so inside any card's containing block).
    expect(container.contains(dialog)).toBe(false);
    expect(document.body.contains(dialog)).toBe(true);
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    // The dialog BOX (not the scrim) carries the role and keeps the old size.
    expect(dialog.className).toMatch(/\bmax-w-3xl\b/);
    expect(dialog.className).not.toMatch(/\binset-0\b/);
  });

  it("source: MediaLightbox uses <Modal> and no longer hand-rolls a fixed inset-0 scrim", () => {
    const s = src("app/creative/page.tsx");
    expect(s).toMatch(/import\s*\{?\s*(default as\s+)?Modal\b[^;]*from\s*"@\/components\/Modal"/);
    const start = s.indexOf("function MediaLightbox(");
    const end = s.indexOf("function SkeletonGrid(", start);
    const body = s.slice(start, end);
    expect(body).toMatch(/<Modal\b/);
    expect(body).not.toMatch(/fixed inset-0/);
  });

  it("anti-vacuity: focus goes in, Tab wraps inside, Escape closes and focus returns to the tile", async () => {
    const { tile } = await openLightbox();
    const dialog = await screen.findByRole("dialog", { name: "sunset.png" });
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
    const items = tabbables(dialog);
    expect(items.length).toBeGreaterThan(1);
    items[items.length - 1].focus();
    fireEvent.keyDown(document.activeElement as Element, { key: "Tab" });
    expect(document.activeElement).toBe(items[0]);
    fireEvent.keyDown(document.activeElement as Element, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(document.activeElement).toBe(tile);
  });

  it("anti-vacuity: the arrow keys still step through the gallery", async () => {
    // Newest first: "dawn" (later) sorts before "sunset".
    await openLightbox([ITEM("dawn", "2026-08-21T10:00:00"), ITEM("sunset", "2026-08-20T10:00:00")]);
    await screen.findByRole("dialog", { name: "dawn.png" });
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "ArrowRight" });
    await screen.findByRole("dialog", { name: "sunset.png" });
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "ArrowLeft" });
    await screen.findByRole("dialog", { name: "dawn.png" });
  });
});

/* ========================================================================== */
/*  Documents — Open / Copy path after a save                                  */
/* ========================================================================== */

const SAVED = "C:\\IJ\\documents\\summary.docx";
const REDACTED = "C:\\in\\organizer.REDACTED.docx";

async function createDocument() {
  hooks.postAnswers["/documents/write"] = { path: SAVED, bytes: 1200 };
  render(<DocumentsPage />);
  fireEvent.change(screen.getByPlaceholderText("summary.docx"), { target: { value: "summary.docx" } });
  fireEvent.change(screen.getByPlaceholderText(/Write or dictate the document body/), {
    target: { value: "Line one" },
  });
  fireEvent.click(screen.getByRole("button", { name: /Create document/ }));
  // The save itself happened (the path is on screen) BEFORE the block is sought,
  // so a red here means "no actions block", never "the form did not submit".
  await screen.findByText(SAVED);
  return await screen.findByTestId("doc-write-saved");
}

async function redactDocument() {
  hooks.postAnswers["/documents/redact/scan"] = {
    source: "C:\\in\\organizer.docx",
    name: "organizer.docx",
    findings: [{ id: 1, category: "ssn", label: "SSN", value: "123-45-6789", count: 1, context: "ssn 123-45-6789 here" }],
    default_output_path: REDACTED,
    suffix: ".REDACTED",
  };
  hooks.postAnswers["/documents/redact/apply"] = {
    path: REDACTED,
    name: "organizer.REDACTED.docx",
    source: "C:\\in\\organizer.docx",
    style: "black",
    counts: { ssn: 1 },
    total: 1,
    note: "",
  };
  render(<DocumentsPage />);
  fireEvent.change(screen.getByLabelText("Document to redact"), { target: { value: "C:\\in\\organizer.docx" } });
  fireEvent.click(screen.getByRole("button", { name: /Scan for PII/ }));
  fireEvent.click(await screen.findByRole("button", { name: /Redact 1 item/ }));
  await screen.findByText(REDACTED); // the write happened first (see createDocument)
  return await screen.findByTestId("doc-redact-saved");
}

const opens = () => hooks.posts.filter((p) => p.path === "/documents/open");

describe("Documents: a saved file can be opened or its path copied, right there", () => {
  it("Create document -> Open asks the daemon to open THAT file", async () => {
    const block = await createDocument();
    // Anti-vacuity: the path and the byte count are still on screen.
    expect(block.textContent ?? "").toContain(SAVED);
    expect(block.textContent ?? "").toMatch(/1,200 bytes/);
    fireEvent.click(within(block).getByRole("button", { name: /^Open$/ }));
    await waitFor(() => expect(opens()).toEqual([{ path: "/documents/open", body: { path: SAVED } }]));
  });

  it("a refused open (4xx) is said inline, never swallowed", async () => {
    hooks.postAnswers["/documents/open"] = new hooks.FakeApiError("That file type cannot be opened here.", 415);
    const block = await createDocument();
    fireEvent.click(within(block).getByRole("button", { name: /^Open$/ }));
    expect(await within(block).findByText(/That file type cannot be opened here\./)).toBeInTheDocument();
  });

  it("Copy path writes the exact path; a refused clipboard does not crash the page", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const block = await createDocument();
    fireEvent.click(within(block).getByRole("button", { name: /Copy path/ }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(SAVED));

    writeText.mockRejectedValueOnce(new Error("denied"));
    fireEvent.click(within(block).getByRole("button", { name: /Copy path/ }));
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId("doc-write-saved")).toBeInTheDocument();
  });

  it("the redacted copy gets the same Open / Copy path; the scan still precedes the write", async () => {
    const block = await redactDocument();
    expect(block.textContent ?? "").toMatch(/Redacted 1 item/);
    expect(block.textContent ?? "").toContain(REDACTED);
    // Anti-vacuity: scan first, then the write with exactly the ticked term.
    const paths = hooks.posts.map((p) => p.path);
    expect(paths.indexOf("/documents/redact/scan")).toBeLessThan(paths.indexOf("/documents/redact/apply"));
    const apply = hooks.posts.find((p) => p.path === "/documents/redact/apply")?.body as Record<string, unknown>;
    expect(apply.terms).toEqual(["123-45-6789"]);
    expect(apply.output_path).toBe(REDACTED);

    fireEvent.click(within(block).getByRole("button", { name: /^Open$/ }));
    await waitFor(() => expect(opens()).toEqual([{ path: "/documents/open", body: { path: REDACTED } }]));
    expect(within(block).getByRole("button", { name: /Copy path/ })).toBeInTheDocument();
  });
});

/* ========================================================================== */
/*  File search — honest default, the user's projects, plain mode words        */
/* ========================================================================== */

const PROJECT_ROOT = "C:\\Work\\ClientBooks";

function renderSearch() {
  hooks.responses["/filesearch/drives"] = { drives: [{ path: "D:\\", label: "Data" }] };
  hooks.responses["/projects"] = {
    projects: [
      { id: "p1", name: "Client Books", brief: "", root: PROJECT_ROOT, status: "active", created_at: "2026-10-01T00:00:00Z", root_exists: true },
      { id: "p2", name: "Old Move", brief: "", root: "C:\\Gone\\Away", status: "active", created_at: "2026-10-01T00:00:00Z", root_exists: false },
    ],
  };
  return render(<FileSearchPage />);
}

const rootSelect = () => screen.getByRole("combobox", { name: /Search in/i }) as HTMLSelectElement;

const searches = () => hooks.gets.filter((g) => g.startsWith("/filesearch?"));

async function runSearch(q = "invoice") {
  const before = searches().length;
  fireEvent.change(screen.getByPlaceholderText(/Search files/), { target: { value: q } });
  const go = screen.getByRole("button", { name: /^Search$/ });
  await waitFor(() => expect(go).not.toBeDisabled()); // the previous search settled
  fireEvent.click(go);
  await waitFor(() => expect(searches().length).toBe(before + 1));
  const url = searches()[before];
  return new URLSearchParams(url.slice(url.indexOf("?") + 1));
}

describe("File search: the default is named honestly and your projects are offered", () => {
  it("the folder-path example shows single backslashes", () => {
    renderSearch();
    const input = screen.getByPlaceholderText(/e\.g\. C:/);
    const ph = input.getAttribute("placeholder") ?? "";
    expect(ph).not.toContain("\\\\"); // two consecutive backslash characters
    expect(ph).toContain("C:\\Users\\me\\Documents");
  });

  it("no 'Project (default)' anywhere — before or after a default search — and no root is sent", async () => {
    renderSearch();
    const def = Array.from(rootSelect().options).find((o) => o.value === "");
    expect(def).toBeDefined();
    expect(def?.textContent ?? "").toMatch(/default folders/i);
    expect(document.body.textContent ?? "").not.toMatch(/Project \(default\)/);
    expect(document.body.textContent ?? "").not.toMatch(/beyond the project/);
    const params = await runSearch();
    // Anti-vacuity: the daemon default is still "no root".
    expect(params.has("root")).toBe(false);
    await waitFor(() => expect(screen.getByText(/No matches/)).toBeInTheDocument());
    expect(document.body.textContent ?? "").not.toMatch(/Project \(default\)/);
  });

  it("'Your projects' lists each project whose folder exists; choosing one searches its root and names it", async () => {
    renderSearch();
    const group = rootSelect().querySelector('optgroup[label="Your projects"]');
    expect(group).not.toBeNull();
    const opts = Array.from((group as HTMLElement).querySelectorAll("option"));
    expect(opts.map((o) => [o.value, o.textContent?.trim()])).toEqual([[PROJECT_ROOT, "Client Books"]]);

    fireEvent.change(rootSelect(), { target: { value: PROJECT_ROOT } });
    // "Searching:" names the project; the raw path stays in a title.
    expect(
      screen.getAllByText("Client Books").some((el) => el.getAttribute("title") === PROJECT_ROOT),
    ).toBe(true);
    const params = await runSearch();
    expect(params.get("root")).toBe(PROJECT_ROOT);
  });

  it("the modes read in plain words with a tooltip; the values sent are unchanged", async () => {
    renderSearch();
    for (const name of ["In contents", "In file names", "By meaning"]) {
      const b = screen.getByRole("button", { name });
      expect((b.getAttribute("title") ?? "").length).toBeGreaterThan(0);
    }
    expect(screen.queryByRole("button", { name: /^semantic$/i })).toBeNull();
    expect((await runSearch()).get("mode")).toBe("content"); // the default
    fireEvent.click(screen.getByRole("button", { name: "By meaning" }));
    expect((await runSearch("tax letter")).get("mode")).toBe("semantic");
    fireEvent.click(screen.getByRole("button", { name: "In file names" }));
    expect((await runSearch("w2")).get("mode")).toBe("name");
  });

  it("anti-vacuity: drives, the custom path (which wins) and Browse… all still work", async () => {
    renderSearch();
    expect(Array.from(rootSelect().options).some((o) => o.value === "D:\\")).toBe(true);
    fireEvent.change(rootSelect(), { target: { value: "D:\\" } });
    expect((await runSearch()).get("root")).toBe("D:\\");
    fireEvent.change(screen.getByPlaceholderText(/e\.g\. C:/), { target: { value: "E:\\Scans" } });
    expect((await runSearch("receipt")).get("root")).toBe("E:\\Scans");
    fireEvent.click(screen.getByRole("button", { name: /Browse/ }));
    expect(screen.getByTestId("picker")).toHaveTextContent("Pick a folder to search in");
  });
});

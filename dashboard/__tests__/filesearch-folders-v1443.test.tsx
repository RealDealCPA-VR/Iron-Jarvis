/**
 * v1.443.0 — File Search looks in the USER's folders and says which.
 *
 * Before: with no folder picked the daemon searched the install's
 * project_root (the app's own data folder in a packaged run), the page called
 * it "Iron Jarvis default folders", and "By meaning" said nothing about
 * needing a local embedding model. Now the page lists the folders a default
 * search covers (chat files folder, active projects, added folders), lets the
 * user add and remove folders (saved as `search_roots` through
 * PUT /settings/values), offers "Also search Documents" in one press, and
 * says what "By meaning" looks in and what it needs.
 */
import React from "react";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const H = vi.hoisted(() => ({
  responses: {} as Record<string, unknown>,
  gets: [] as string[],
  puts: [] as { path: string; body?: unknown }[],
  putFails: false,
  pickPath: "E:\\Clients",
}));

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (H.responses[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));
vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(m: string, s: number) {
      super(m);
      this.status = s;
    }
  }
  return {
    ApiError,
    get: (path: string) => {
      H.gets.push(path);
      return Promise.resolve({ results: [] });
    },
    put: (path: string, body?: unknown) => {
      H.puts.push({ path, body });
      return H.putFails ? Promise.reject(new ApiError("Folder refused.", 400)) : Promise.resolve({});
    },
  };
});
vi.mock("@/components/VoiceInput", () => ({
  VoiceInput: () => null,
  appendDictation: (p: string, c: string) => p + c,
}));
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title, subtitle }: { title: string; subtitle?: React.ReactNode }) => (
    <div>
      <h1>{title}</h1>
      <p>{subtitle}</p>
    </div>
  ),
}));
vi.mock("@/components/FilePickerModal", () => ({
  FilePickerModal: ({
    open,
    title,
    onPick,
    onClose,
  }: {
    open: boolean;
    title?: string;
    onPick: (p: string) => void;
    onClose: () => void;
  }) =>
    open ? (
      <div data-testid="picker">
        {title}
        <button
          type="button"
          onClick={() => {
            onPick(H.pickPath);
            onClose();
          }}
        >
          pick it
        </button>
      </div>
    ) : null,
}));

const FileSearchPage = (await import("@/app/filesearch/page")).default;

const DOCS = "C:\\Users\\me\\OneDrive\\Documents";

function setup(values: Record<string, unknown> = { search_roots: [], chat_files_root: "" }) {
  H.responses["/filesearch/drives"] = {
    drives: [
      { path: "C:\\", label: "C:" },
      { path: "C:\\Users\\me", label: "Home" },
      { path: DOCS, label: "Documents" },
    ],
  };
  H.responses["/projects"] = {
    projects: [
      { id: "p1", name: "Client Books", root: "C:\\Work\\Books", status: "active", root_exists: true },
      { id: "p2", name: "Old Year", root: "C:\\Work\\Old", status: "archived", root_exists: true },
      { id: "p3", name: "Gone", root: "C:\\Gone", status: "active", root_exists: false },
    ],
  };
  H.responses["/settings/values"] = { values };
  return render(<FileSearchPage />);
}

const folders = () => screen.getByTestId("filesearch-folders");
const rows = () => Array.from(folders().querySelectorAll("li"));
const searching = () => screen.getByTestId("filesearch-searching");

beforeEach(() => {
  H.responses = {};
  H.gets = [];
  H.puts = [];
  H.putFails = false;
  H.pickPath = "E:\\Clients";
});

describe("File Search says which of YOUR folders it looks in", () => {
  it("lists the chat files folder, active projects and added folders; never 'default folders'", () => {
    setup({ search_roots: ["D:\\Scans"], chat_files_root: "" });
    expect(rows().map((r) => [r.getAttribute("data-kind"), r.textContent])).toEqual([
      ["chat_files", `Iron Jarvis chat files${DOCS}\\Iron Jarvisalways`],
      ["project", "Client BooksC:\\Work\\Booksa project"],
      ["added", "ScansD:\\Scansadded"],
    ]);
    expect(searching()).toHaveTextContent("Iron Jarvis chat files, Client Books, Scans");
    expect(document.body.textContent ?? "").not.toMatch(/default folders/i);
    expect(document.body.textContent ?? "").toMatch(/own data folder is never searched/);
  });

  it("a moved chat files folder is shown where it really is", () => {
    setup({ search_roots: [], chat_files_root: "F:\\Jarvis files" });
    expect(rows()[0]).toHaveTextContent("F:\\Jarvis files");
  });

  it("removing an added folder saves the rest and says so", async () => {
    setup({ search_roots: ["D:\\Scans", "E:\\Clients"], chat_files_root: "" });
    fireEvent.click(screen.getByRole("button", { name: "Stop searching Scans" }));
    await waitFor(() =>
      expect(H.puts).toEqual([
        { path: "/settings/values", body: { values: { search_roots: ["E:\\Clients"] } } },
      ]),
    );
    await waitFor(() => expect(screen.getByText(/Saved\. The next search uses it\./)).toBeInTheDocument());
    expect(rows().some((r) => r.textContent?.includes("D:\\Scans"))).toBe(false);
    // The chat files folder and projects are not removable here.
    expect(within(rows()[0]).queryByRole("button")).toBeNull();
  });

  it("'Also search Documents' adds the real Documents folder in one press, then goes away", async () => {
    setup();
    fireEvent.click(screen.getByRole("button", { name: /Also search Documents/ }));
    await waitFor(() =>
      expect(H.puts).toEqual([{ path: "/settings/values", body: { values: { search_roots: [DOCS] } } }]),
    );
    await waitFor(() => expect(screen.queryByRole("button", { name: /Also search Documents/ })).toBeNull());
    expect(rows().at(-1)?.textContent).toContain("Documents");
  });

  it("is not offered when Documents is already added (any spelling)", () => {
    setup({ search_roots: [DOCS.toLowerCase() + "\\"], chat_files_root: "" });
    expect(screen.queryByRole("button", { name: /Also search Documents/ })).toBeNull();
  });

  it("Add a folder… saves the picked folder; Browse… only picks a place for one search", async () => {
    setup();
    fireEvent.click(screen.getByRole("button", { name: /Add a folder/ }));
    expect(screen.getByTestId("picker")).toHaveTextContent("Pick a folder to add to File Search");
    fireEvent.click(screen.getByRole("button", { name: "pick it" }));
    await waitFor(() =>
      expect(H.puts).toEqual([
        { path: "/settings/values", body: { values: { search_roots: ["E:\\Clients"] } } },
      ]),
    );
    H.pickPath = "G:\\Once";
    fireEvent.click(screen.getByRole("button", { name: /Browse/ }));
    expect(screen.getByTestId("picker")).toHaveTextContent("Pick a folder to search in");
    fireEvent.click(screen.getByRole("button", { name: "pick it" }));
    expect(H.puts).toHaveLength(1);
    expect((screen.getByPlaceholderText(/e\.g\. C:/) as HTMLInputElement).value).toBe("G:\\Once");
  });

  it("a refused save says why and keeps the list as it was", async () => {
    H.putFails = true;
    setup({ search_roots: ["D:\\Scans"], chat_files_root: "" });
    fireEvent.click(screen.getByRole("button", { name: "Stop searching Scans" }));
    await waitFor(() => expect(screen.getByText("Folder refused.")).toBeInTheDocument());
    expect(rows().some((r) => r.textContent?.includes("D:\\Scans"))).toBe(true);
  });
});

describe("'By meaning' says what it looks in and what it needs", () => {
  it("names the added folders only, and the local embedding model it needs", () => {
    setup({ search_roots: ["D:\\Scans"], chat_files_root: "" });
    expect(screen.queryByTestId("filesearch-meaning-note")).toBeNull(); // content mode: no note
    fireEvent.click(screen.getByRole("button", { name: "By meaning" }));
    expect(searching()).toHaveTextContent("your added folders (Scans)");
    const note = screen.getByTestId("filesearch-meaning-note");
    expect(note).toHaveTextContent(/needs a local embedding model/);
    expect(note).toHaveTextContent(/ollama pull nomic-embed-text/);
    expect(note).toHaveTextContent(/Settings › Connections/);
  });

  it("with no added folder, an empty answer says why instead of 'No matches.'", async () => {
    setup();
    fireEvent.click(screen.getByRole("button", { name: "By meaning" }));
    expect(searching()).toHaveTextContent("none yet, add a folder below");
    fireEvent.change(screen.getByPlaceholderText(/Search files/), { target: { value: "tax letter" } });
    fireEvent.click(screen.getByRole("button", { name: /^Search$/ }));
    await waitFor(() =>
      expect(screen.getByText("Nothing to search by meaning yet: add a folder below.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("No matches.")).toBeNull();
  });

  it("control: a contents search with nothing found still says 'No matches.'", async () => {
    setup();
    fireEvent.change(screen.getByPlaceholderText(/Search files/), { target: { value: "invoice" } });
    fireEvent.click(screen.getByRole("button", { name: /^Search$/ }));
    await waitFor(() => expect(screen.getByText("No matches.")).toBeInTheDocument());
    const url = H.gets.find((g) => g.startsWith("/filesearch?")) ?? "";
    expect(new URLSearchParams(url.slice(url.indexOf("?") + 1)).has("root")).toBe(false);
  });
});

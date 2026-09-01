import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/**
 * v1.222.0 — the Workflows page lists SAVED workflows with a visible Delete.
 *
 * The user reported having no way to delete a workflow. The route existed and
 * a delete handler existed, behind a trash icon at opacity 0 inside the
 * canvas's Load ▾ dropdown. This list is the fix, and its delete is honest:
 * the confirm step asks GET /workflows/{name}/references and names the
 * schedules / reflex rules that will start failing, or says the check
 * failed — never "nothing uses it" on a guess.
 */

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    calls: [] as string[],
    dels: [] as string[],
    responses: {} as Record<string, unknown>,
    delResponses: {} as Record<string, unknown>,
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    api.calls.push(path);
    const r = api.responses[path];
    if (r === undefined) {
      return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 0));
    }
    if (r instanceof api.FakeApiError) return Promise.reject(r);
    return Promise.resolve(r);
  },
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: (path: string) => {
    api.dels.push(path);
    const r = api.delResponses[path];
    if (r instanceof api.FakeApiError) return Promise.reject(r);
    return Promise.resolve(r ?? {});
  },
}));

import {
  SavedWorkflows,
  WORKFLOWS_LIST_EVENT,
  referencesSentence,
  savedStepCount,
} from "@/components/workflow/SavedWorkflows";

const LIST = {
  workflows: [
    {
      id: "wf_1",
      name: "nightly-close",
      description: "close the books",
      steps_json: JSON.stringify([{ name: "a" }, { name: "b" }]),
    },
    { id: "wf_2", name: "odd-row", steps_json: "not json" },
  ],
};

beforeEach(() => {
  window.scrollTo = vi.fn();
  api.responses["/workflows"] = LIST;
});

afterEach(() => {
  cleanup();
  api.calls = [];
  api.dels = [];
  api.responses = {};
  api.delResponses = {};
});

describe("helpers", () => {
  it("savedStepCount tolerates missing and malformed steps_json", () => {
    expect(savedStepCount(JSON.stringify([1, 2, 3]))).toBe(3);
    expect(savedStepCount("not json")).toBe(0);
    expect(savedStepCount(undefined)).toBe(0);
    expect(savedStepCount(JSON.stringify({ not: "a list" }))).toBe(0);
  });

  it("referencesSentence names each automation and what happens to it", () => {
    expect(referencesSentence([])).toBe("");
    const s = referencesSentence([
      { kind: "schedule", name: "close-books" },
      { kind: "reflex", name: "on-upload" },
    ]);
    expect(s).toContain("schedule “close-books”");
    expect(s).toContain("reflex rule “on-upload”");
    expect(s).toContain("will fail");
  });
});

describe("SavedWorkflows", () => {
  it("lists every saved workflow with its step count", async () => {
    render(<SavedWorkflows />);
    expect(await screen.findByText("nightly-close")).toBeInTheDocument();
    expect(screen.getByText(/2 steps · close the books/)).toBeInTheDocument();
    // A malformed row still renders (0 steps) instead of taking the list down.
    expect(screen.getByText("odd-row")).toBeInTheDocument();
    expect(screen.getByText(/0 steps/)).toBeInTheDocument();
  });

  it("Load hands the saved def to the canvas through ij:load-workflow", async () => {
    const seen: unknown[] = [];
    const onLoad = (e: Event) => seen.push((e as CustomEvent).detail);
    window.addEventListener("ij:load-workflow", onLoad);
    try {
      render(<SavedWorkflows />);
      await screen.findByText("nightly-close");
      fireEvent.click(screen.getByTitle("Load “nightly-close” into the editor"));
      expect(seen).toEqual([
        {
          name: "nightly-close",
          description: "close the books",
          steps_json: LIST.workflows[0].steps_json,
        },
      ]);
    } finally {
      window.removeEventListener("ij:load-workflow", onLoad);
    }
  });

  it("Delete asks first, names what still fires the workflow, then deletes and announces", async () => {
    api.responses["/workflows/nightly-close/references"] = {
      name: "nightly-close",
      references: [{ kind: "schedule", name: "close-books", enabled: true }],
    };
    const announced: unknown[] = [];
    const onList = (e: Event) => announced.push((e as CustomEvent).detail);
    window.addEventListener(WORKFLOWS_LIST_EVENT, onList);
    try {
      render(<SavedWorkflows />);
      await screen.findByText("nightly-close");
      fireEvent.click(screen.getByRole("button", { name: "Delete nightly-close" }));
      // Nothing is deleted by the first click.
      expect(api.dels).toEqual([]);
      expect(await screen.findByText(/Still used by schedule “close-books”/)).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: /Delete workflow/ }));
      await waitFor(() => expect(api.dels).toEqual(["/workflows/nightly-close"]));
      await screen.findByText(/Deleted “nightly-close”/);
      expect(announced).toEqual([{ deleted: "nightly-close" }]);
      // The list re-fetched after the delete.
      expect(api.calls.filter((p) => p === "/workflows").length).toBeGreaterThanOrEqual(2);
    } finally {
      window.removeEventListener(WORKFLOWS_LIST_EVENT, onList);
    }
  });

  it("says the reference check FAILED rather than claiming nothing uses it", async () => {
    api.responses["/workflows/nightly-close/references"] = new api.FakeApiError("boom", 500);
    render(<SavedWorkflows />);
    await screen.findByText("nightly-close");
    fireEvent.click(screen.getByRole("button", { name: "Delete nightly-close" }));
    expect(await screen.findByText(/Couldn’t check/)).toBeInTheDocument();
    expect(screen.queryByText(/Nothing scheduled or automated uses it/)).not.toBeInTheDocument();
  });

  it("Cancel backs out without deleting", async () => {
    api.responses["/workflows/nightly-close/references"] = { references: [] };
    render(<SavedWorkflows />);
    await screen.findByText("nightly-close");
    fireEvent.click(screen.getByRole("button", { name: "Delete nightly-close" }));
    expect(await screen.findByText(/Nothing scheduled or automated uses it/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Cancel/ }));
    expect(screen.queryByTestId("confirm-delete-nightly-close")).not.toBeInTheDocument();
    expect(api.dels).toEqual([]);
  });

  it("refreshes when the canvas announces a change", async () => {
    render(<SavedWorkflows />);
    await screen.findByText("nightly-close");
    const before = api.calls.filter((p) => p === "/workflows").length;
    api.responses["/workflows"] = {
      workflows: [{ id: "wf_9", name: "brand-new", steps_json: "[]" }],
    };
    window.dispatchEvent(new CustomEvent(WORKFLOWS_LIST_EVENT, { detail: { saved: "brand-new" } }));
    expect(await screen.findByText("brand-new")).toBeInTheDocument();
    expect(api.calls.filter((p) => p === "/workflows").length).toBeGreaterThan(before);
  });

  it("offline is UNKNOWN, not 'no saved workflows'", async () => {
    api.responses["/workflows"] = new api.FakeApiError("offline", 0);
    render(<SavedWorkflows />);
    expect(await screen.findByText(/daemon looks offline/)).toBeInTheDocument();
    expect(screen.queryByText(/Nothing saved yet/)).not.toBeInTheDocument();
  });
});

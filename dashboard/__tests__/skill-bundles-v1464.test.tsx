/**
 * v1.464.0: "Starter skills" on the Templates page.
 *
 * The owner asked for starter skill bundles a business can choose to load and
 * a way to pull skills from GitHub with a warning. These tests drive the real
 * component against the shapes the real daemon sends (the Python test file
 * tests/test_skill_bundles_v1464.py drives the same routes).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  calls: [] as { method: string; path: string; body?: unknown }[],
  bundles: null as unknown,
  loadAnswer: null as unknown,
  preview: null as unknown,
  previewError: "" as string,
}));

vi.mock("@/lib/api", () => ({
  get: vi.fn(async (path: string) => {
    H.calls.push({ method: "GET", path });
    if (path === "/templates/skill-bundles") return H.bundles;
    throw new Error("not mocked: " + path);
  }),
  post: vi.fn(async (path: string, body?: unknown) => {
    H.calls.push({ method: "POST", path, body });
    if (path === "/templates/skill-bundles/github/preview") {
      if (H.previewError) throw new Error(H.previewError);
      return H.preview;
    }
    return H.loadAnswer;
  }),
}));

import { SkillBundles } from "@/components/templates/SkillBundles";
import {
  bundleSummary,
  decodeBundles,
  decodeGithubPreview,
  loadWords,
  removeWords,
} from "@/lib/skillBundles";

const WARNING =
  "These skills were written by someone else. A skill is a set of instructions Jarvis follows, so only load skills from a source you trust, and read each one first. Only the instructions are downloaded: a skill's scripts and other files are not, and nothing from the repository is run.";

const skill = (name: string, state = "not_loaded") => ({ name, description: `What ${name} does.`, state });

function seed() {
  H.calls.length = 0;
  H.previewError = "";
  H.bundles = {
    github_warning: WARNING,
    bundles: [
      {
        id: "accounting",
        name: "Accounting",
        blurb: "Bookkeeping and close work.",
        order: 2,
        loaded: 0,
        skills: [skill("month-end-close-checklist"), skill("bank-reconciliation-review"), skill("variance-analysis")],
      },
      {
        id: "coding",
        name: "Coding",
        blurb: "Working in a code folder.",
        order: 4,
        loaded: 2,
        skills: [skill("code-review", "loaded"), skill("release-notes", "edited"), skill("refactor-safely")],
      },
    ],
  };
  H.loadAnswer = { bundle: "accounting", loaded: ["month-end-close-checklist"], skipped: [] };
  H.preview = {
    repo: "acme/skills",
    commit: "a".repeat(40),
    more: 0,
    warning: WARNING,
    skills: [
      { path: "skills/invoice-chaser/SKILL.md", name: "invoice-chaser", description: "Chase invoices.", can_load: true, why: "", other_files: 2, state: "new" },
      { path: "skills/bad/SKILL.md", name: "helper", description: "A helper.", can_load: false, why: "it holds text that reads as an attempt to override Iron Jarvis's rules", other_files: 0, state: "new" },
    ],
  };
}

beforeEach(seed);
afterEach(cleanup);

const posts = (path: string) => H.calls.filter((c) => c.method === "POST" && c.path === path);

describe("the bundles", () => {
  it("lists each bundle with what it holds and what is loaded", async () => {
    render(<SkillBundles />);
    expect(await screen.findByTestId("skill-bundle-accounting")).toBeTruthy();
    expect(screen.getByTestId("skill-bundle-summary-accounting").textContent).toBe("3 skills");
    expect(screen.getByTestId("skill-bundle-summary-coding").textContent).toBe("3 skills, 2 loaded");
    // Nothing loaded: no Remove. Something loaded: Remove, and "Load the rest".
    expect(screen.queryByTestId("skill-bundle-remove-accounting")).toBeNull();
    expect(screen.getByTestId("skill-bundle-remove-coding")).toBeTruthy();
    expect(screen.getByTestId("skill-bundle-load-accounting").textContent).toBe("Load all");
    expect(screen.getByTestId("skill-bundle-load-coding").textContent).toBe("Load the rest");
  });

  it("Load all sends no list, says what happened, and reads the bundles again", async () => {
    render(<SkillBundles />);
    fireEvent.click(await screen.findByTestId("skill-bundle-load-accounting"));
    await waitFor(() =>
      expect(screen.getByTestId("skill-bundles-note").textContent).toBe(
        "Loaded 1 skill. Type / in Chat to use one.",
      ),
    );
    expect(posts("/templates/skill-bundles/accounting/load")[0].body).toEqual({});
    await waitFor(() =>
      expect(H.calls.filter((c) => c.method === "GET").length).toBeGreaterThanOrEqual(2),
    );
  });

  it("only the ticked skills are sent, and a skill already loaded cannot be ticked", async () => {
    render(<SkillBundles />);
    const row = await screen.findByTestId("skill-bundle-coding");
    fireEvent.click(within(row).getByRole("button", { name: /Coding/ }));
    const loaded = within(row).getByLabelText("Load code-review") as HTMLInputElement;
    expect(loaded.disabled).toBe(true);
    expect(within(row).getByText("Loaded, and you changed it")).toBeTruthy();

    const acc = screen.getByTestId("skill-bundle-accounting");
    fireEvent.click(within(acc).getByRole("button", { name: /Accounting/ }));
    fireEvent.click(within(acc).getByLabelText("Load variance-analysis"));
    fireEvent.click(screen.getByTestId("skill-bundle-load-chosen-accounting"));
    await waitFor(() => expect(posts("/templates/skill-bundles/accounting/load").length).toBe(1));
    expect(posts("/templates/skill-bundles/accounting/load")[0].body).toEqual({
      skills: ["month-end-close-checklist", "bank-reconciliation-review"],
    });
  });

  it("Remove takes two presses", async () => {
    H.loadAnswer = { bundle: "coding", removed: ["code-review"], kept: [{ name: "release-notes", reason: "you changed it, so it was kept" }] };
    render(<SkillBundles />);
    const btn = await screen.findByTestId("skill-bundle-remove-coding");
    fireEvent.click(btn);
    expect(posts("/templates/skill-bundles/coding/remove").length).toBe(0);
    fireEvent.click(screen.getByTestId("skill-bundle-remove-coding"));
    await waitFor(() => expect(posts("/templates/skill-bundles/coding/remove").length).toBe(1));
    await waitFor(() =>
      expect(screen.getByTestId("skill-bundles-note").textContent).toBe(
        "Removed 1 skill. They were moved to Iron Jarvis's trash folder, not deleted. Kept: release-notes (you changed it, so it was kept).",
      ),
    );
  });

  it("draws nothing when Iron Jarvis has no bundles to offer (an older version)", async () => {
    H.bundles = { bundles: [] };
    const { container } = render(<SkillBundles />);
    await waitFor(() => expect(H.calls.length).toBe(1));
    expect(container.textContent).toBe("");
  });
});

describe("skills from GitHub", () => {
  async function openGithub() {
    render(<SkillBundles />);
    const row = await screen.findByTestId("skill-bundle-github");
    fireEvent.click(within(row).getByRole("button", { name: /Load skills from GitHub/ }));
    return row;
  }

  it("shows the warning before anything is asked of GitHub", async () => {
    await openGithub();
    expect(screen.getByTestId("skill-bundle-github-warning").textContent).toBe(WARNING);
    expect(posts("/templates/skill-bundles/github/preview").length).toBe(0);
  });

  it("looks first, then loads only on the second press, with the previewed version", async () => {
    await openGithub();
    fireEvent.change(screen.getByLabelText("GitHub repository address"), {
      target: { value: "https://github.com/acme/skills" },
    });
    fireEvent.click(screen.getByTestId("skill-bundle-github-look"));
    const listed = await screen.findByTestId("skill-bundle-github-list");
    expect(within(listed).getByText("2 other files in its folder will not be downloaded.")).toBeTruthy();
    // The flagged skill cannot be ticked, and says why.
    expect((within(listed).getByLabelText("Load helper") as HTMLInputElement).disabled).toBe(true);
    expect(within(listed).getByText(/Cannot be loaded: it holds text that reads as an attempt/)).toBeTruthy();

    H.loadAnswer = { repo: "acme/skills", loaded: ["invoice-chaser"], skipped: [] };
    const press = screen.getByTestId("skill-bundle-github-load");
    expect(press.textContent).toBe("Load 1 skill");
    fireEvent.click(press);
    // First press arms: nothing is sent yet.
    expect(posts("/templates/skill-bundles/github/load").length).toBe(0);
    expect(screen.getByTestId("skill-bundle-github-armed").textContent).toBe(
      "Press again to load. You have read these skills and trust where they come from.",
    );
    expect(screen.getByTestId("skill-bundle-github-load").textContent).toBe("Yes, load 1 skill");
    fireEvent.click(screen.getByTestId("skill-bundle-github-load"));
    await waitFor(() => expect(posts("/templates/skill-bundles/github/load").length).toBe(1));
    expect(posts("/templates/skill-bundles/github/load")[0].body).toEqual({
      url: "https://github.com/acme/skills",
      commit: "a".repeat(40),
      paths: ["skills/invoice-chaser/SKILL.md"],
      confirm: true,
    });
  });

  it("changing the address or the ticks takes the armed press back", async () => {
    await openGithub();
    const box = screen.getByLabelText("GitHub repository address");
    fireEvent.change(box, { target: { value: "https://github.com/acme/skills" } });
    fireEvent.click(screen.getByTestId("skill-bundle-github-look"));
    await screen.findByTestId("skill-bundle-github-list");
    fireEvent.click(screen.getByTestId("skill-bundle-github-load"));
    expect(screen.getByTestId("skill-bundle-github-armed")).toBeTruthy();
    fireEvent.change(box, { target: { value: "https://github.com/acme/other" } });
    expect(screen.queryByTestId("skill-bundle-github-armed")).toBeNull();
    expect(posts("/templates/skill-bundles/github/load").length).toBe(0);
  });

  it("says GitHub's refusal in its own words", async () => {
    H.previewError = "GitHub has no public repository, branch or folder at that address. A private repository cannot be read.";
    await openGithub();
    fireEvent.change(screen.getByLabelText("GitHub repository address"), {
      target: { value: "https://github.com/acme/none" },
    });
    fireEvent.click(screen.getByTestId("skill-bundle-github-look"));
    expect((await screen.findByTestId("skill-bundle-github-problem")).textContent).toBe(H.previewError);
    expect(screen.queryByTestId("skill-bundle-github-list")).toBeNull();
  });
});

describe("the decoders and words", () => {
  it("never invent a field the daemon did not send", () => {
    expect(decodeBundles(null)).toEqual([]);
    expect(decodeBundles({ bundles: [{ id: "x", skills: [] }] })).toEqual([]);
    const [b] = decodeBundles({ bundles: [{ id: "x", skills: [{ name: "a", state: "weird" }] }] });
    expect(b.skills[0].state).toBe("not_loaded");
    // A preview with no real commit id is not a preview.
    expect(decodeGithubPreview({ commit: "main", skills: [] })).toBeNull();
    expect(decodeGithubPreview({ commit: "a".repeat(40), skills: [{ path: "p", name: "n" }] })!.skills[0].canLoad).toBe(false);
  });

  it("say what happened in one plain line", () => {
    expect(bundleSummary({ id: "x", name: "X", blurb: "", loaded: 5, skills: Array(5).fill(skill("a", "loaded")) as never })).toBe("5 skills, all loaded");
    expect(loadWords({ loaded: [], skipped: [] })).toBe("Nothing was loaded.");
    expect(loadWords({ loaded: ["a", "b"], skipped: [{ name: "c", reason: "you already have a skill with this name" }] })).toBe(
      "Loaded 2 skills. Type / in Chat to use one. Not loaded: c (you already have a skill with this name).",
    );
    expect(removeWords({ removed: [], kept: [] })).toBe("Nothing was removed.");
  });
});

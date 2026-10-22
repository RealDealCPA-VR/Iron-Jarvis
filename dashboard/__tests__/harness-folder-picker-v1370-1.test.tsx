/**
 * v1.370.1 — the New session folder picker opens folders; it never starts a
 * session by itself.
 *
 * The user's report: "I'm not able to select a proper subfolder that has a
 * project in it, it stops at a top level folder … it stops right here at VR
 * and does not allow me to drill down." Reproduced on a scratch daemon:
 * clicking `C:\Users` showed "This folder cannot be used: … not writable",
 * and clicking `VR` CREATED a session in `C:\Users\VR`. The cause: the
 * folder tree's row is a <button> with no `type`, and the New session panel
 * draws the tree inside its <form>, so every row click also SUBMITTED the
 * form with the folder just clicked. The tree's own tests passed (no form
 * around them) and the sidebar's tests mocked the tree away.
 *
 * Driven here through the REAL NewSessionForm with the REAL DirectoryTree:
 * opening `Users` then `VR` posts nothing, shows VR's folders, and fills
 * the folder box; only Start starts the session.
 */

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const LISTS: Record<string, { name: string; path: string; is_project: string | null }[]> = {
  "C:\\": [
    { name: "Users", path: "C:\\Users", is_project: null },
    { name: "Windows", path: "C:\\Windows", is_project: null },
  ],
  "C:\\Users": [
    { name: "Public", path: "C:\\Users\\Public", is_project: null },
    { name: "VR", path: "C:\\Users\\VR", is_project: null },
  ],
  "C:\\Users\\VR": [
    { name: "Projects", path: "C:\\Users\\VR\\Projects", is_project: null },
  ],
  "C:\\Users\\VR\\Projects": [
    { name: "Iron-Jarvis", path: "C:\\Users\\VR\\Projects\\Iron-Jarvis", is_project: "git" },
  ],
};

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status = 0;
  }
  return {
    ApiError,
    get: (p: string) => {
      if (p.startsWith("/fs/list")) {
        const path = decodeURIComponent(new URL(p, "http://x").searchParams.get("path") ?? "");
        return Promise.resolve({ path, entries: LISTS[path] ?? [] });
      }
      return Promise.reject(new ApiError(`unmocked ${p}`));
    },
  };
});
vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path === "/fs/drives" ? { drives: [{ path: "C:\\", label: "C:" }] } : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));

import { HarnessSidebar } from "@/components/harness/HarnessSidebar";

afterEach(() => cleanup());

function renderSidebar() {
  const onStart = vi.fn(async () => null);
  render(
    <HarnessSidebar
      folders={[]}
      listError={null}
      selectedId={null}
      onSelect={() => {}}
      onStart={onStart}
      onRename={async () => null}
      defaultFolder={null}
      terminals={[]}
      onOpenTerminal={() => {}}
    />,
  );
  fireEvent.click(screen.getByTestId("harness-new-session"));
  return { onStart, form: screen.getByTestId("harness-new-session-form") };
}

async function openRow(form: HTMLElement, path: string) {
  const row = await waitFor(() => {
    const el = within(form).getByTitle(path);
    expect(el.tagName).toBe("BUTTON");
    return el;
  });
  fireEvent.click(row);
  return row;
}

describe("the New session folder picker", () => {
  it("drills down Users > VR > Projects without starting a session, and Start uses the folder picked", async () => {
    const { onStart, form } = renderSidebar();
    await openRow(form, "C:\\Users");
    await openRow(form, "C:\\Users\\VR");
    await openRow(form, "C:\\Users\\VR\\Projects");
    // VR opened: its folder is listed and pickable, three levels down.
    await waitFor(() => expect(within(form).getByTitle("C:\\Users\\VR\\Projects\\Iron-Jarvis")).toBeTruthy());
    expect(onStart).not.toHaveBeenCalled();
    // The box follows the clicks; nothing was refused on the way down.
    await openRow(form, "C:\\Users\\VR\\Projects\\Iron-Jarvis");
    expect((within(form).getByRole("textbox", { name: /folder/i }) as HTMLInputElement).value)
      .toBe("C:\\Users\\VR\\Projects\\Iron-Jarvis");
    expect(onStart).not.toHaveBeenCalled();
    // Only Start starts it, in the folder picked.
    fireEvent.click(screen.getByTestId("harness-new-start"));
    await waitFor(() => expect(onStart).toHaveBeenCalledWith("C:\\Users\\VR\\Projects\\Iron-Jarvis"));
    expect(onStart).toHaveBeenCalledTimes(1);
  });

  it("every button the tree draws inside the form is a plain button, never a submit", async () => {
    const { form } = renderSidebar();
    await openRow(form, "C:\\Users");
    await waitFor(() => expect(within(form).getByTitle("C:\\Users\\VR")).toBeTruthy());
    const start = screen.getByTestId("harness-new-start");
    const submits = Array.from(form.querySelectorAll("button")).filter(
      (b) => b !== start && (b.getAttribute("type") ?? "submit") === "submit",
    );
    expect(submits.map((b) => b.getAttribute("title") || b.textContent)).toEqual([]);
  });
});

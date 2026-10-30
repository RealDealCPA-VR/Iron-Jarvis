/**
 * v1.409.0 (found live) — Build's Code view, New session: pasting a folder
 * path and pressing Enter (or Go) only BROWSED the tree to it; "Folder: pick
 * one below" stayed until the folder's row was clicked. DirectoryTree's new
 * opt-in `pickTypedPath` makes Enter / Go also pick it. Every other picker
 * keeps browsing only (the prop is off by default) — both pinned here on the
 * REAL tree.
 */

import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status = 0;
  }
  return {
    ApiError,
    get: (p: string) => {
      if (p.startsWith("/fs/list")) return Promise.resolve({ entries: [] });
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

import { DirectoryTree } from "@/components/terminal/DirectoryTree";

afterEach(() => cleanup());

function typePath(value: string) {
  const box = screen.getByLabelText("Go to root path");
  fireEvent.change(box, { target: { value } });
  return box;
}

describe("the path box", () => {
  it("with pickTypedPath, Enter picks the pasted folder (quotes stripped)", () => {
    const onSelect = vi.fn();
    render(<DirectoryTree variant="calm" hideAction pickTypedPath selectedPath={null} onSelect={onSelect} />);
    const box = typePath('"C:\\work\\shop"');
    fireEvent.keyDown(box, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("C:\\work\\shop");
  });

  it("with pickTypedPath, Go picks it too", () => {
    const onSelect = vi.fn();
    render(<DirectoryTree variant="calm" hideAction pickTypedPath selectedPath={null} onSelect={onSelect} />);
    typePath("D:\\clients");
    fireEvent.click(screen.getByRole("button", { name: "Go" }));
    expect(onSelect).toHaveBeenCalledWith("D:\\clients");
  });

  it("CONTROL: every other picker keeps browsing only (no prop, nothing picked)", () => {
    const onSelect = vi.fn();
    render(<DirectoryTree variant="calm" hideAction selectedPath={null} onSelect={onSelect} />);
    const box = typePath("C:\\work\\shop");
    fireEvent.keyDown(box, { key: "Enter" });
    fireEvent.click(screen.getByRole("button", { name: "Go" }));
    expect(onSelect).not.toHaveBeenCalled();
    // ...it still browsed there: the pasted root is the tree's root now.
    expect((screen.getByLabelText("Root directory") as HTMLSelectElement).value).toBe("C:\\work\\shop");
  });
});

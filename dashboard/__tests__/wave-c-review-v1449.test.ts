/**
 * The review of Wave C's two pure helpers (v1.449.0, readiness Wave C items
 * 23 and 31).
 *
 * - `runtimeDownload`: the daemon's own address wins ONLY when it is an
 *   https address; anything else (http, javascript:, a bare word) falls back
 *   to the table — a download link is never pointed at a swap.
 * - `staleAddonLine`: only a RELEASE number on both sides is compared. A
 *   dev build ("1.448.1-dev"), a bare word or an empty string says nothing;
 *   the numbers are compared as numbers ("1.9.0" is older than "1.10.0").
 */

import { describe, expect, it } from "vitest";

import { isDownloadAddress, runtimeDownload } from "@/lib/directoryLinks";
import { BROWSERS, compareVersions, staleAddonLine } from "@/components/browser/browserWords";

describe("runtimeDownload (item 23)", () => {
  it("takes the daemon's address only when it is https", () => {
    expect(runtimeDownload("Node.js", "https://nodejs.org/en/download")?.href).toBe("https://nodejs.org/en/download");
    expect(runtimeDownload("Node.js", "http://nodejs.org")?.href).toBe("https://nodejs.org");
    expect(runtimeDownload("Node.js", "javascript:alert(1)")?.href).toBe("https://nodejs.org");
    expect(runtimeDownload("Node.js", "nodejs.org")?.href).toBe("https://nodejs.org");
    expect(runtimeDownload("Node.js", "   ")?.href).toBe("https://nodejs.org");
  });

  it("an unknown program with an https address still gets a link; without one, none", () => {
    expect(runtimeDownload("Deno", "https://deno.com")).toEqual({ label: "Get Deno (free)", href: "https://deno.com" });
    expect(runtimeDownload("Deno", "http://deno.com")).toBeNull();
    expect(runtimeDownload("Deno")).toBeNull();
    expect(runtimeDownload("")).toBeNull();
  });

  it("isDownloadAddress is https with a host", () => {
    expect(isDownloadAddress("https://bun.sh")).toBe(true);
    expect(isDownloadAddress("http://bun.sh")).toBe(false);
    expect(isDownloadAddress("https://")).toBe(false);
    expect(isDownloadAddress("file:///C:/x.exe")).toBe(false);
    expect(isDownloadAddress(null)).toBe(false);
  });
});

describe("staleAddonLine (item 31)", () => {
  it("compares release numbers as numbers", () => {
    expect(compareVersions("1.9.0", "1.10.0")).toBe(-1);
    expect(compareVersions("1.10.0", "1.9.0")).toBe(1);
    expect(compareVersions("v1.448.1", "1.448.1")).toBe(0);
    expect(staleAddonLine("1.9.0", "1.10.0", BROWSERS.chrome)).toContain("older copy");
  });

  it("a number that is not a release number says nothing — on either side", () => {
    expect(compareVersions("1.448.1-dev", "1.449.0")).toBeNull();
    expect(compareVersions("1.449.0", "1.449.0-dev")).toBeNull();
    expect(compareVersions("1.448", "1.449.0")).toBeNull();
    expect(compareVersions("dev", "1.449.0")).toBeNull();
    expect(staleAddonLine("1.448.1-dev", "1.449.0", BROWSERS.edge)).toBeNull();
    expect(staleAddonLine("1.440.0", "1.449.0-dev", BROWSERS.edge)).toBeNull();
    expect(staleAddonLine("", "1.449.0", BROWSERS.edge)).toBeNull();
    expect(staleAddonLine("1.440.0", "", BROWSERS.edge)).toBeNull();
  });

  it("an equal or newer add-on says nothing", () => {
    expect(staleAddonLine("1.449.0", "1.449.0", BROWSERS.edge)).toBeNull();
    expect(staleAddonLine("1.450.0", "1.449.0", BROWSERS.edge)).toBeNull();
  });
});

"use client";

/**
 * The "@" menu's Files section (calm chat wave 4, v1.329.0).
 *
 * The calm-chat proposal said "@ offers files, folders and other
 * conversations"; until now files and folders were only in the "+" menu. In a
 * chat that works inside a project, "@" now also offers the project folder's
 * files and folders:
 *
 * - a FILE is attached exactly as "+ Attach" attaches an upload: the same
 *   paperclip chip in the composer card and the same `attachments` field on
 *   the request (the daemon reads an absolute path through the same file
 *   policy it applies to an upload);
 * - a FOLDER becomes the chat's working folder, exactly as "+ Choose a working
 *   folder" does (`workspace_dir`).
 *
 * The listing is GET /fs/files on the project's folder with the SAME query the
 * project panel's Files tab sends (`components/terminal/FilesPanel.tsx`), so
 * the daemon's short memo answers both. That route lists the newest files
 * first (bounded walk: 4 levels, 600 files), so this offers what is there and
 * recent, matched by what is typed after "@"; folders are the ones those files
 * live in. No project (or a project whose folder is missing) = no section.
 *
 * Its own module (not in useChatStream or the page) because tests mock
 * `@/lib/useChatStream` with a fixed export list.
 */

import { useEffect, useRef, useState } from "react";
import { get } from "@/lib/api";

/** How many rows the Files section shows (folders and files together). */
export const AT_FILES_ROWS_MAX = 6;
/** With nothing typed after "@", at most this many of those rows are folders. */
export const AT_FILES_IDLE_FOLDERS = 2;
/** The Files tab's own query (FilesPanel), so one daemon memo serves both. */
const FILES_DEPTH = 4;
const FILES_LIMIT = 600;

/** One file the daemon listed. */
export interface ProjectFile {
  name: string;
  path: string;
  /** Path inside the project folder, "/"-separated. */
  rel: string;
  size: number;
}

/** What GET /fs/files answered, whitelisted. */
export interface ProjectListing {
  root: string;
  files: ProjectFile[];
}

/** A row the menu draws. */
export interface ProjectEntry {
  kind: "file" | "folder";
  name: string;
  /** Absolute path (what is attached, or the working folder). */
  path: string;
  /** Path inside the project folder, "/"-separated. */
  rel: string;
  /** Bytes, for a file (the attachment chip shows it). */
  size?: number;
}

/** The listing path for a project folder (the Files tab's exact query). */
export function projectFilesPath(root: string): string {
  return `/fs/files?path=${encodeURIComponent(root)}&depth=${FILES_DEPTH}&limit=${FILES_LIMIT}`;
}

/** GET /fs/files's answer by whitelist: string name, path and rel, a size
 *  (0 when absent), at most {@link FILES_LIMIT}. Anything else is null. */
export function decodeProjectFiles(raw: unknown): ProjectListing | null {
  if (!raw || typeof raw !== "object") return null;
  const { root, files } = raw as { root?: unknown; files?: unknown };
  if (typeof root !== "string" || !root.trim() || !Array.isArray(files)) return null;
  const out: ProjectFile[] = [];
  for (const f of files) {
    if (!f || typeof f !== "object") continue;
    const { name, path, rel, size } = f as {
      name?: unknown;
      path?: unknown;
      rel?: unknown;
      size?: unknown;
    };
    if (typeof name !== "string" || typeof path !== "string" || typeof rel !== "string") continue;
    if (!name || !path || !rel) continue;
    out.push({
      name,
      path,
      rel: rel.replace(/\\/g, "/"),
      size: typeof size === "number" && Number.isFinite(size) && size >= 0 ? size : 0,
    });
    if (out.length >= FILES_LIMIT) break;
  }
  return { root, files: out };
}

/** Every folder the listed files live in (each ancestor inside the project),
 *  in the order a file first names it, so the newest work comes first. */
export function projectFolders(listing: ProjectListing): ProjectEntry[] {
  const root = listing.root.replace(/[\\/]+$/, "");
  const sep = root.includes("\\") || /^[A-Za-z]:$/.test(root) ? "\\" : "/";
  const seen = new Set<string>();
  const out: ProjectEntry[] = [];
  for (const f of listing.files) {
    const parts = f.rel.split("/").filter(Boolean);
    for (let i = 1; i < parts.length; i++) {
      const rel = parts.slice(0, i).join("/");
      if (seen.has(rel)) continue;
      seen.add(rel);
      out.push({ kind: "folder", name: parts[i - 1], path: root + sep + parts.slice(0, i).join(sep), rel });
    }
  }
  return out;
}

/** How well `name`/`rel` match `q`: 0 the name starts with it, 1 the name
 *  holds it, 2 only the path inside the project does; null no match. */
function score(name: string, rel: string, q: string): number | null {
  const n = name.toLowerCase();
  if (n.startsWith(q)) return 0;
  if (n.includes(q)) return 1;
  if (rel.toLowerCase().includes(q)) return 2;
  return null;
}

/**
 * The rows the Files section draws for what is typed after "@" (`query`,
 * already lower case). Nothing typed: a couple of folders, then the newest
 * files. Something typed: the folders and files whose name or path holds it,
 * best match first (a name that starts with it, then one that holds it, then
 * a path), folders before files at the same rank, newest first within that.
 * `attached` paths are not offered again.
 */
export function matchProjectEntries(
  listing: ProjectListing | null,
  query: string,
  attached: readonly string[] = [],
  max: number = AT_FILES_ROWS_MAX,
): ProjectEntry[] {
  if (!listing) return [];
  const q = query.trim().toLowerCase();
  const taken = new Set(attached);
  const files: ProjectEntry[] = listing.files
    .filter((f) => !taken.has(f.path))
    .map((f) => ({ kind: "file", name: f.name, path: f.path, rel: f.rel, size: f.size }));
  const folders = projectFolders(listing);
  if (!q) {
    const lead = folders.filter((f) => !f.rel.includes("/")).slice(0, AT_FILES_IDLE_FOLDERS);
    return [...lead, ...files].slice(0, max);
  }
  const ranked: { e: ProjectEntry; s: number; i: number }[] = [];
  [...folders, ...files].forEach((e, i) => {
    const s = score(e.name, e.rel, q);
    if (s !== null) ranked.push({ e, s, i });
  });
  ranked.sort((a, b) => a.s - b.s || a.i - b.i);
  return ranked.slice(0, max).map((r) => r.e);
}

/** Two folder paths name the same folder: slashes either way, no trailing
 *  separator, and case ignored (Windows paths are case-blind). */
export function sameFolderPath(a: string, b: string): boolean {
  const norm = (p: string) => p.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
  return norm(a) === norm(b);
}

/** Where a row sits inside the project, for its quiet second part: the
 *  folder that holds it ("" at the top of the project). */
export function entryWhere(e: ProjectEntry): string {
  const i = e.rel.lastIndexOf("/");
  return i > 0 ? e.rel.slice(0, i) : "";
}

/**
 * The project folder's listing while the "@" menu is open: null until the
 * answer arrives, and null with no folder. Asked once each time the menu
 * opens (typing narrows what was listed; it does not ask again). An answer
 * for a closed menu or another folder is dropped; a failure lists nothing.
 */
export function useProjectFiles(open: boolean, root: string | null): ProjectListing | null {
  const [listing, setListing] = useState<ProjectListing | null>(null);
  const seqRef = useRef(0);
  useEffect(() => {
    const seq = ++seqRef.current;
    if (!open || !root) {
      setListing(null);
      return;
    }
    get<unknown>(projectFilesPath(root))
      .then((d) => {
        if (seq === seqRef.current) setListing(decodeProjectFiles(d));
      })
      .catch(() => {
        if (seq === seqRef.current) setListing(null);
      });
  }, [open, root]);
  return listing;
}

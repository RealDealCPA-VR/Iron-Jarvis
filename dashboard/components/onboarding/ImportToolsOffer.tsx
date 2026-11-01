"use client";

/**
 * "Import your tools" on the first-run checklist (v1.420.0, C6.12).
 *
 * A new user who already set up tools in Claude Code, Claude Desktop, Codex,
 * Cursor or VS Code was never told, at first run, that Iron Jarvis can bring
 * them over: the door existed only on the Tools page and in the Directory.
 * The checklist's optional "Connect the apps you use" step now reads the SAME
 * answer those doors read (GET /mcp/import/sources, read-only, names only)
 * and, when it finds a tool not added yet, offers "Import your tools" — the
 * Tools page's own `SourcesDoor`, opened in place. Nothing is added without a
 * press: the door lists the tools, the user ticks, then presses Add.
 *
 * Silent when nothing new is found, while the read is out, or when it fails
 * (the step's own Directory link still stands). The door's module loads only
 * when the user opens it.
 */

import { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { FolderInput } from "lucide-react";
import { get } from "@/lib/api";
import { discover, discoveryLine } from "@/lib/importDiscovery";
import type { ImportSource } from "@/lib/mcpImport";

const SourcesDoor = dynamic(() => import("@/components/tools/AddPack").then((m) => m.SourcesDoor), {
  ssr: false,
});

/** The offer's button words. */
export const IMPORT_TOOLS_LABEL = "Import your tools";

/** One read of the import sources; [] when it cannot be read (an older
 *  daemon, a failed read), never a throw. */
async function readSources(): Promise<ImportSource[]> {
  try {
    const res = await get<{ sources?: ImportSource[] }>("/mcp/import/sources");
    return Array.isArray(res?.sources) ? res.sources : [];
  } catch {
    return []; // a failed read offers nothing; the step's link stands
  }
}

export function ImportToolsOffer() {
  const [sources, setSources] = useState<ImportSource[] | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let live = true;
    void readSources().then((s) => {
      if (live) setSources(s);
    });
    return () => {
      live = false;
    };
  }, []);

  const found = useMemo(() => discover(sources), [sources]);
  if (!sources || found.fresh === 0) return null;
  return (
    <div data-testid="first-run-import" className="mt-2 space-y-2">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
        <FolderInput size={13} className="shrink-0 text-accent-soft" aria-hidden="true" />
        <span data-testid="first-run-import-line" className="text-zinc-300">
          {discoveryLine(found)}
        </span>
        <button
          type="button"
          data-testid="first-run-import-open"
          aria-expanded={open}
          aria-controls={open ? "first-run-import-door" : undefined}
          onClick={() => setOpen((v) => !v)}
          className="rounded-lg border border-accent/30 bg-accent/[0.08] px-2.5 py-1 text-xs font-medium text-accent-soft transition-colors hover:bg-accent/[0.14]"
        >
          {open ? "Hide" : IMPORT_TOOLS_LABEL}
        </button>
      </div>
      {open && (
        <div id="first-run-import-door">
          <SourcesDoor sources={sources} onAdded={() => void readSources().then(setSources)} />
        </div>
      )}
    </div>
  );
}

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

/**
 * v1.455.0 (readiness Wave H item 29): the chat home's apps line carries the
 * same offer (`testPrefix="home-apps-import"`, `centered`), so a user who
 * set up tools elsewhere is told on the first screen, not only in the
 * checklist. The scan now also reads the LAST WORKING FOLDER's own files
 * (`.mcp.json`, `.cursor/`, `.vscode/`…) when no project is active.
 */
export function ImportToolsOffer({
  testPrefix = "first-run-import",
  centered = false,
}: {
  testPrefix?: string;
  centered?: boolean;
} = {}) {
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
    <div data-testid={testPrefix} className="mt-2 space-y-2">
      <div className={`flex flex-wrap items-center gap-x-2 gap-y-1 text-xs${centered ? " justify-center" : ""}`}>
        <FolderInput size={13} className="shrink-0 text-accent-soft" aria-hidden="true" />
        <span data-testid={`${testPrefix}-line`} className="text-zinc-300">
          {discoveryLine(found)}
        </span>
        <button
          type="button"
          data-testid={`${testPrefix}-open`}
          aria-expanded={open}
          aria-controls={open ? `${testPrefix}-door` : undefined}
          onClick={() => setOpen((v) => !v)}
          className="rounded-lg border border-accent/30 bg-accent/[0.08] px-2.5 py-1 text-xs font-medium text-accent-soft transition-colors hover:bg-accent/[0.14]"
        >
          {open ? "Hide" : IMPORT_TOOLS_LABEL}
        </button>
      </div>
      {open && (
        <div id={`${testPrefix}-door`} className={centered ? "text-left" : undefined}>
          <SourcesDoor sources={sources} onAdded={() => void readSources().then(setSources)} />
        </div>
      )}
    </div>
  );
}

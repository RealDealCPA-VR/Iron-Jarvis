"use client";

/**
 * v1.343.0 ("add a pack in one paste"), lane K1: the "Add a pack" area of the
 * Tools page. Three doors, ONE preview list:
 *
 *   (a) Bring your tools from Claude, Codex, Cursor…: GET
 *       /mcp/import/sources, one group per app that has tools (label + file
 *       path); a file that could not be read is one quiet line, the places
 *       with nothing in them fold into one "Also looked in" line.
 *   (b) Paste from a website or README (advanced): one box that takes
 *       anything (a README's {"mcpServers": ...} block, a Codex TOML, an npx
 *       line, a URL); POST /mcp/import/preview renders the rows, "Add
 *       selected" posts POST /mcp/import/apply with the names and the typed
 *       overrides.
 *   (c) HTTP server: a URL (the transport read off it: /sse means SSE, else
 *       HTTP), with the server type, header rows and a token under More
 *       options -> POST /mcp/servers.
 *
 * v1.354.0 (G49, "import that explains itself"): the door a new user meets
 * FIRST is (a); every row says in one line what the tool does (the catalog's
 * blurb the daemon matched by package, else "A tool from <app>") and keeps
 * the launch command and key names under Details; the outcome lists the tool
 * names a pack loaded and where to switch it on in a chat; a pack that did
 * not start offers a button to its row (and Install Node.js when the reason
 * names it). (G50) a server its app has switched off is listed unticked,
 * "turned off in <app>"; one already connected here another way reads
 * "already connected" / "already a memory source". Words: lib/addPackWords.
 *
 * VALUES NEVER RENDER. A preview row carries key and header NAMES only; the
 * override boxes are password fields whose text goes to the apply route and
 * nowhere else. The text the user pasted is re-sent to the apply route as
 * is (the daemon re-parses it; the browser never holds a parsed value).
 */

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { Check, ChevronRight, ClipboardPaste, FolderInput, Globe, Plus, TriangleAlert, X } from "lucide-react";
import { ApiError, get, post } from "@/lib/api";
import { Badge, ErrorNote, LoaderInline, SuccessNote, WarnNote } from "@/components/ui";
import { addedButNotStarted, packDidNotStart } from "@/lib/packStartWords";
import { appName, listWords } from "@/lib/importDiscovery";
import {
  DOOR_LABELS,
  PASTE_APP,
  TOOLS_ROWS_HREF,
  USE_IN_CHAT,
  existsWords,
  isOff,
  mentionsNode,
  offWords,
  rowAbout,
  showPackRow,
  splitRowTail,
  startsTicked,
  toolNamesLine,
  transportFromUrl,
  type ImportRow,
} from "@/lib/addPackWords";
import { StatusChip } from "./chips";
import { RUNTIME_HELP } from "./meta";
import {
  httpServerBody,
  nameFromUrl,
  rowLaunch,
  rowStatus,
  safeName,
  type ApplyResult,
  type ImportSource,
  type PreviewResult,
} from "@/lib/mcpImport";

type Door = "paste" | "sources" | "http";

/** v1.354.0 (G49): the sources door first, the paste box named as the
 *  advanced way. */
const DOORS: { id: Door; label: string; icon: ReactNode }[] = [
  { id: "sources", label: DOOR_LABELS.sources, icon: <FolderInput size={13} aria-hidden /> },
  { id: "paste", label: DOOR_LABELS.paste, icon: <ClipboardPaste size={13} aria-hidden /> },
  { id: "http", label: DOOR_LABELS.http, icon: <Globe size={13} aria-hidden /> },
];

/** Overrides as typed: pack name -> KEY -> value. Only non-empty values
 *  reach the route. */
type Overrides = Record<string, Record<string, string>>;

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err);
}

function cleanOverrides(ov: Overrides, names: string[]): Overrides {
  const out: Overrides = {};
  for (const n of names) {
    const kv = ov[n] ?? {};
    const kept: Record<string, string> = {};
    for (const [k, v] of Object.entries(kv)) if (v !== "") kept[k] = v;
    if (Object.keys(kept).length > 0) out[n] = kept;
  }
  return out;
}

/** The quiet fold the rows keep their launch details in (the Tools page's
 *  "Last output" fold, same look). */
const FOLD_SUMMARY =
  "flex w-fit cursor-pointer select-none items-center gap-1 text-[11px] font-medium text-zinc-500 transition-colors hover:text-zinc-300 [&::-webkit-details-marker]:hidden";
const FOLD_CHEVRON = "transition-transform duration-200 group-open:rotate-90 motion-reduce:transition-none";

/** An action inside a sentence (Install Node.js, Open its row): a link look,
 *  the accent ink, never a box. */
const INLINE_ACTION =
  "font-medium text-accent-soft underline underline-offset-2 transition-colors hover:text-accent";

/** "Install Node.js" (the catalog's own download page), beside a reason or a
 *  note that names Node. */
function InstallNode({ testId }: { testId: string }) {
  return (
    <a href={RUNTIME_HELP.Node.href} target="_blank" rel="noreferrer" data-testid={testId} className={INLINE_ACTION}>
      Install Node.js
    </a>
  );
}

/* ---------------------------------------------------------------- rows */

/** The one preview list every door ends in. `app` names where the rows came
 *  from ("A tool from <app>", "turned off in <app>"); `detailsOpen` opens
 *  each row's Details (the paste door: the user typed the config). */
function PreviewList({
  rows,
  selected,
  onToggle,
  overrides,
  onOverride,
  app,
  showOrigin = false,
  detailsOpen = false,
}: {
  rows: ImportRow[];
  selected: Set<string>;
  onToggle: (name: string) => void;
  overrides: Overrides;
  onOverride: (name: string, key: string, value: string) => void;
  app: string;
  showOrigin?: boolean;
  detailsOpen?: boolean;
}) {
  if (rows.length === 0) return null;
  return (
    <ul className="mt-3 space-y-2" data-testid="import-preview">
      {rows.map((row) => {
        const st = rowStatus(row);
        const isHttp = row.transport === "http" || row.transport === "sse";
        const launcher = isHttp ? row.transport.toUpperCase() : (row.command ?? "").trim() || "stdio";
        const secretKeys = row.secret_keys ?? [];
        const envKeys = (row.env_keys ?? []).filter((k) => !secretKeys.includes(k));
        const headerNames = row.header_names ?? [];
        const off = isOff(row);
        return (
          <li
            key={`${row.origin ?? ""}:${row.name}`}
            data-testid={`import-row-${row.name}`}
            data-off={off ? "1" : undefined}
            className="rounded-xl border border-white/[0.06] bg-white/[0.015] px-3 py-2"
          >
            <div className="flex flex-wrap items-center gap-2">
              <input
                type="checkbox"
                data-testid={`import-pick-${row.name}`}
                checked={selected.has(row.name)}
                onChange={() => onToggle(row.name)}
                aria-label={`Add ${row.name}`}
                className="h-4 w-4 accent-accent"
              />
              <span className="font-mono text-sm font-semibold text-zinc-100">{row.name}</span>
              {st.status === "added" ? (
                <Badge value={existsWords(row)} tone="slate" variant="calm" keepCase data-testid={`import-exists-${row.name}`} />
              ) : off ? (
                <Badge value={offWords(app)} tone="slate" variant="calm" keepCase data-testid={`import-off-${row.name}`} />
              ) : st.status === "available" ? null : (
                <StatusChip status={st.status} needs={st.needs} />
              )}
              {secretKeys.length > 0 && (
                <Badge value={`${secretKeys.length} in vault`} tone="violet" variant="calm" keepCase />
              )}
            </div>
            {/* v1.354.0 (G49): what it does, in one line. */}
            <p className="mt-1 text-[12px] text-zinc-400" data-testid={`import-about-${row.name}`}>
              {rowAbout(row, app)}
            </p>
            {row.launcher_note && (
              <p className="mt-1 text-[12px] text-tone-warn" data-testid={`import-note-${row.name}`}>
                {row.launcher_note}
                {mentionsNode(row.launcher_note) && (
                  <>
                    {" "}
                    <InstallNode testId={`import-install-node-${row.name}`} />
                  </>
                )}
              </p>
            )}
            {/* v1.354.0 (G49): the launch command and the key names, folded. */}
            <details className="group mt-1" open={detailsOpen || undefined} data-testid={`import-details-${row.name}`}>
              <summary className={FOLD_SUMMARY}>
                <ChevronRight size={12} className={FOLD_CHEVRON} aria-hidden />
                Details
              </summary>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <Badge value={launcher} tone="slate" variant="calm" keepCase data-testid={`import-launcher-${row.name}`} />
                <div className="min-w-0 flex-1 overflow-x-auto">
                  <code className="whitespace-pre font-mono text-[11px] text-zinc-500">{rowLaunch(row)}</code>
                </div>
              </div>
              {showOrigin && row.origin && row.origin !== "paste" && (
                <p className="mt-1 truncate text-[11px] text-zinc-600" title={row.origin}>
                  from {row.origin}
                </p>
              )}
              {(envKeys.length > 0 || headerNames.length > 0) && (
                <p className="mt-1 text-[11px] text-zinc-500">
                  {envKeys.length > 0 && <span>Uses {envKeys.join(", ")}</span>}
                  {envKeys.length > 0 && headerNames.length > 0 && <span> and </span>}
                  {headerNames.length > 0 && <span>headers {headerNames.join(", ")}</span>}
                </p>
              )}
              {secretKeys.length > 0 && (
                <div className="mt-2 space-y-1">
                  {secretKeys.map((key) => (
                    <label key={key} className="flex items-center gap-2">
                      <span className="w-44 shrink-0 truncate font-mono text-[11px] text-zinc-400">{key}</span>
                      <input
                        type="password"
                        autoComplete="off"
                        data-testid={`override-${row.name}-${key}`}
                        value={overrides[row.name]?.[key] ?? ""}
                        onChange={(e) => onOverride(row.name, key, e.target.value)}
                        placeholder="Type a value if the config has none"
                        className="field flex-1 px-2 py-1 font-mono text-[12px]"
                      />
                    </label>
                  ))}
                  <span className="block text-[11px] leading-relaxed text-zinc-500">
                    Kept in the encrypted vault, never in the config file.
                  </span>
                </div>
              )}
            </details>
          </li>
        );
      })}
    </ul>
  );
}

/** v1.354.0 (G49): the way to a pack's row from the outcome. On the Tools
 *  page (`rowsHere`) a button that scrolls to the row below, opens its last
 *  output and puts the focus on its Retry; elsewhere (the Directory) a link
 *  to the Tools page's packs. */
function RowJump({ name, label, rowsHere }: { name: string; label: string; rowsHere: boolean }) {
  const [missed, setMissed] = useState(false);
  if (!rowsHere) {
    return (
      <Link href={TOOLS_ROWS_HREF} data-testid={`import-open-row-${name}`} className={INLINE_ACTION}>
        Open its row in Tools to retry.
      </Link>
    );
  }
  return (
    <>
      <button
        type="button"
        data-testid={`import-open-row-${name}`}
        onClick={() => setMissed(!showPackRow(name))}
        className={INLINE_ACTION}
      >
        {label}
      </button>
      {missed && (
        <span role="status" className="text-zinc-500" data-testid={`import-row-missing-${name}`}>
          Its row is under Connected packs below.
        </span>
      )}
    </>
  );
}

/** The tool names each pack loaded, read ONCE after an apply that started
 *  at least one pack (GET /mcp/servers `tool_names`). `null` until read;
 *  a failed read leaves the outcome as it was (counts only). */
function useToolNames(result: ApplyResult): Record<string, string[]> | null {
  const [names, setNames] = useState<Record<string, string[]> | null>(null);
  useEffect(() => {
    if (!result.added.some((a) => !packDidNotStart(a))) return;
    let live = true;
    get<{ servers?: { name?: unknown; tool_names?: unknown }[] }>("/mcp/servers")
      .then((res) => {
        if (!live) return;
        const map: Record<string, string[]> = {};
        for (const s of res?.servers ?? []) {
          if (s && typeof s.name === "string" && Array.isArray(s.tool_names)) {
            map[s.name] = s.tool_names.filter((n): n is string => typeof n === "string");
          }
        }
        setNames(map);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [result]);
  return names;
}

/** What apply answered, row by row. v1.351.0 (G08): a pack that loaded NO
 *  tools was saved but did not start, so its row carries the warning triangle
 *  and "Added, but it didn't start: <why>." in the warn tone, never the green
 *  check. v1.354.0 (G49): "Open its row below to retry." is a button to the
 *  row, a started pack names its tools, and the outcome ends with where to
 *  switch them on in a chat. */
function ApplyOutcome({ result, rowsHere }: { result: ApplyResult; rowsHere: boolean }) {
  const toolNames = useToolNames(result);
  const anyStarted = result.added.some((a) => !packDidNotStart(a));
  return (
    <div className="mt-3 space-y-1" data-testid="import-outcome">
      {result.added.map((a) => {
        const notStarted = packDidNotStart(a);
        const { head, tail } = splitRowTail(addedButNotStarted(a.note));
        const names = toolNamesLine(toolNames?.[a.name]);
        return (
          <div
            key={`added:${a.name}`}
            data-testid={`import-result-${a.name}`}
            data-outcome={notStarted ? "not-started" : "ok"}
            className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-zinc-300"
          >
            {notStarted ? (
              <TriangleAlert size={12} className="text-tone-warn" aria-hidden data-testid={`import-warn-${a.name}`} />
            ) : (
              <Check size={12} className="text-tone-success" aria-hidden />
            )}
            <span className="font-mono font-semibold text-zinc-100">{a.name}</span>
            {notStarted ? (
              <>
                <span className="text-tone-warn">{head}</span>{" "}
                {tail && <RowJump name={a.name} label={tail} rowsHere={rowsHere} />}
                {mentionsNode(a.note) && <InstallNode testId={`import-install-node-${a.name}`} />}
              </>
            ) : a.note ? (
              <span className="text-tone-warn">{a.note}</span>
            ) : (
              <span data-testid={`import-tools-${a.name}`}>
                {a.tools_loaded} tool{a.tools_loaded === 1 ? "" : "s"} ready
                {names ? `: ${names}` : ""}
              </span>
            )}
          </div>
        );
      })}
      {result.skipped.map((s) => (
        <div
          key={`skipped:${s.name}`}
          data-testid={`import-skipped-${s.name}`}
          className="flex flex-wrap items-center gap-2 text-[12px] text-zinc-500"
        >
          <X size={12} aria-hidden />
          <span className="font-mono text-zinc-300">{s.name}</span>
          <span>skipped: {s.why}</span>
        </div>
      ))}
      {anyStarted && (
        <p className="pt-1 text-[12px] text-zinc-500" data-testid="import-use-in-chat">
          {USE_IN_CHAT}
        </p>
      )}
    </div>
  );
}

/* ------------------------------------------------------------- selection */

/** The rows ticked when a list arrives: new here and switched on in their
 *  app (v1.354.0: a server its app turned off starts unticked). */
function pickable(rows: ImportRow[]): Set<string> {
  return new Set(rows.filter(startsTicked).map((r) => r.name));
}

function useSelection(initial: ImportRow[] = []) {
  const [selected, setSelected] = useState<Set<string>>(() => pickable(initial));
  const [overrides, setOverrides] = useState<Overrides>({});
  const seed = (rows: ImportRow[]) => {
    setSelected(pickable(rows));
    setOverrides({});
  };
  const toggle = (name: string) =>
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  const setOverride = (name: string, key: string, value: string) =>
    setOverrides((o) => ({ ...o, [name]: { ...(o[name] ?? {}), [key]: value } }));
  // A typed value is consumed by the apply: once it has gone to the vault
  // it leaves the page (a controlled input keeps its value in the markup).
  const clearOverrides = () => setOverrides({});
  /** v1.353.0 (G42): drop names that became "already added" (a re-read
   *  after an import), so "Add selected (N)" never counts them again. The
   *  same Set is kept when nothing changes (no extra render). */
  const unpick = (names: string[]) =>
    setSelected((s) => {
      if (!names.some((n) => s.has(n))) return s;
      const next = new Set(s);
      for (const n of names) next.delete(n);
      return next;
    });
  return { selected, overrides, seed, toggle, setOverride, clearOverrides, unpick };
}

/* ------------------------------------------------------------ paste door */

function PasteDoor({ onAdded, rowsHere }: { onAdded: () => void; rowsHere: boolean }) {
  const [text, setText] = useState("");
  const [previewed, setPreviewed] = useState<string>("");
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [busy, setBusy] = useState<"preview" | "apply" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ApplyResult | null>(null);
  const sel = useSelection();

  async function runPreview(raw: string) {
    const t = raw.trim();
    if (!t || t === previewed || busy) return;
    setBusy("preview");
    setError(null);
    setResult(null);
    try {
      const res = await post<PreviewResult>("/mcp/import/preview", { text: t });
      setPreview(res);
      setPreviewed(t);
      sel.seed(res.servers ?? []);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(null);
    }
  }

  async function apply() {
    if (!preview || busy) return;
    const names = (preview.servers ?? []).filter((r) => sel.selected.has(r.name)).map((r) => r.name);
    if (names.length === 0) return;
    setBusy("apply");
    setError(null);
    try {
      const res = await post<ApplyResult>("/mcp/import/apply", {
        text: previewed,
        names,
        overrides: cleanOverrides(sel.overrides, names),
        auto_approve: false,
        replace: false,
      });
      setResult(res);
      sel.clearOverrides();
      onAdded();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(null);
    }
  }

  const rows: ImportRow[] = preview?.servers ?? [];
  const chosen = rows.filter((r) => sel.selected.has(r.name)).length;

  return (
    <div data-testid="add-pack-paste-door">
      <p className="mb-2 text-[13px] text-zinc-500">
        Paste a config block from a README, a Claude or Codex config, an npx line, or a server URL.
        Nothing is added until you choose the packs and press Add.
      </p>
      <textarea
        data-testid="add-pack-paste"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => void runPreview(text)}
        onPaste={(e) => {
          const pasted = e.clipboardData?.getData("text") ?? "";
          if (pasted.trim()) {
            const next = text ? `${text}\n${pasted}` : pasted;
            setText(next);
            e.preventDefault();
            void runPreview(next);
          }
        }}
        rows={4}
        spellCheck={false}
        aria-label="Config to paste"
        placeholder={'{"mcpServers": {...}}   or   npx -y @org/server   or   https://host/mcp'}
        className="field resize-y font-mono text-[12px]"
      />
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <button
          type="button"
          data-testid="add-pack-preview"
          onClick={() => void runPreview(text)}
          disabled={!text.trim() || busy !== null}
          className="btn-soft btn-sm"
        >
          {busy === "preview" ? <LoaderInline label="Reading" /> : "Preview"}
        </button>
        {preview && preview.unrecognised && rows.length === 0 && (
          <span className="text-[12px] text-tone-warn" data-testid="add-pack-unrecognised">
            Nothing in that text looks like a server config.
          </span>
        )}
      </div>
      {error && (
        <div className="mt-2">
          <ErrorNote>{error}</ErrorNote>
        </div>
      )}
      <PreviewList
        rows={rows}
        selected={sel.selected}
        onToggle={sel.toggle}
        overrides={sel.overrides}
        onOverride={sel.setOverride}
        app={PASTE_APP}
        detailsOpen
      />
      {rows.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button
            type="button"
            data-testid="add-pack-apply"
            onClick={() => void apply()}
            disabled={chosen === 0 || busy !== null}
            className="btn-accent"
          >
            {busy === "apply" ? (
              <LoaderInline label="Adding" />
            ) : (
              <>
                <Plus size={14} /> Add selected ({chosen})
              </>
            )}
          </button>
          <span className="text-[11px] text-zinc-600">A pack that is already added is skipped.</span>
        </div>
      )}
      {result && <ApplyOutcome result={result} rowsHere={rowsHere} />}
    </div>
  );
}

/* ---------------------------------------------------------- sources door */

function SourceGroup({
  source,
  onAdded,
  rowsHere,
}: {
  source: ImportSource;
  onAdded: () => void;
  rowsHere: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ApplyResult | null>(null);
  const rows: ImportRow[] = source.servers ?? [];
  const sel = useSelection(rows);
  // v1.353.0 (G42): the Directory re-reads the list after an import; a row
  // that is now "already added" leaves the selection. Keyed on the names
  // (a string), so a fresh `[]` each render never re-runs it.
  const addedKey = rows
    .filter((r) => r.exists)
    .map((r) => r.name)
    .join("\n");
  const { unpick } = sel;
  useEffect(() => {
    if (addedKey) unpick(addedKey.split("\n"));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `unpick` is a fresh closure each render; the names are the trigger
  }, [addedKey]);

  async function apply() {
    const names = rows.filter((r) => sel.selected.has(r.name)).map((r) => r.name);
    if (names.length === 0 || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await post<ApplyResult>("/mcp/import/apply", {
        source: source.source,
        names,
        overrides: cleanOverrides(sel.overrides, names),
        auto_approve: false,
        replace: false,
      });
      setResult(res);
      sel.clearOverrides();
      onAdded();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const chosen = rows.filter((r) => sel.selected.has(r.name)).length;

  if (!source.readable) {
    return (
      <div data-testid={`import-source-${source.source}`} className="text-[12px] text-zinc-500">
        <span className="text-zinc-400">{source.label}</span>: {source.note ? source.note : "not found"}
        {source.origin ? <span className="text-zinc-600"> ({source.origin})</span> : null}
      </div>
    );
  }

  return (
    <div
      data-testid={`import-source-${source.source}`}
      className="rounded-xl border border-white/[0.06] bg-white/[0.01] px-3 py-2"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[13px] font-medium text-zinc-200">{source.label}</span>
        <span className="truncate font-mono text-[11px] text-zinc-500" title={source.origin}>
          {source.origin}
        </span>
      </div>
      {rows.length === 0 ? (
        <p className="mt-1 text-[12px] text-zinc-500">No tools in this file.</p>
      ) : (
        <>
          <PreviewList
            rows={rows}
            selected={sel.selected}
            onToggle={sel.toggle}
            overrides={sel.overrides}
            onOverride={sel.setOverride}
            app={appName(source)}
            showOrigin
          />
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <button
              type="button"
              data-testid={`import-source-apply-${source.source}`}
              onClick={() => void apply()}
              disabled={chosen === 0 || busy}
              className="btn-accent"
            >
              {busy ? (
                <LoaderInline label="Adding" />
              ) : (
                <>
                  <Plus size={14} /> Add selected ({chosen})
                </>
              )}
            </button>
          </div>
        </>
      )}
      {error && (
        <div className="mt-2">
          <ErrorNote>{error}</ErrorNote>
        </div>
      )}
      {result && <ApplyOutcome result={result} rowsHere={rowsHere} />}
    </div>
  );
}

/** How many app names the "Also looked in" line spells out before "N more". */
const LOOKED_IN_NAMES = 4;

/** v1.354.0 (G50): the places that hold nothing to bring (no such file, or a
 *  file with no tools in it), folded into ONE quiet line so the apps that do
 *  have tools lead. Each place keeps its own line (and testid) inside. */
function LookedIn({ sources }: { sources: ImportSource[] }) {
  if (sources.length === 0) return null;
  const apps: string[] = [];
  for (const s of sources) {
    const name = appName(s);
    if (!apps.includes(name)) apps.push(name);
  }
  const named =
    apps.length > LOOKED_IN_NAMES
      ? `${apps.slice(0, LOOKED_IN_NAMES).join(", ")} and ${apps.length - LOOKED_IN_NAMES} more`
      : listWords(apps);
  return (
    <details className="group" data-testid="import-looked-in">
      <summary className={FOLD_SUMMARY}>
        <ChevronRight size={12} className={FOLD_CHEVRON} aria-hidden />
        Also looked in {named}: nothing to bring
      </summary>
      <div className="mt-1 space-y-1 pl-4">
        {sources.map((s) => (
          <div key={s.source} data-testid={`import-source-${s.source}`} className="text-[12px] text-zinc-500">
            <span className="text-zinc-400">{s.label}</span>: {s.readable ? "no tools in this file" : "not found"}
            {s.origin ? <span className="text-zinc-600"> ({s.origin})</span> : null}
          </div>
        ))}
      </div>
    </details>
  );
}

/**
 * The "Bring your tools from Claude, Codex, Cursor…" door. v1.353.0 (G42):
 * exported so the Directory (Settings › Connections › Apps & tools) opens the
 * SAME door under its discovery line. `sources` given (the list the caller
 * already read; `null` while it loads) = no second read here, and a re-read
 * after an import flows straight in; left out = the door reads it itself, as
 * on the Tools page. `rowsHere` (v1.354.0): the Tools page's pack rows are
 * on this page, so "Open its row" scrolls to one instead of going there.
 */
export function SourcesDoor({
  onAdded,
  sources: given,
  rowsHere = false,
}: {
  onAdded: () => void;
  sources?: ImportSource[] | null;
  rowsHere?: boolean;
}) {
  const controlled = given !== undefined;
  const [fetched, setFetched] = useState<ImportSource[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (controlled) return;
    let live = true;
    get<{ sources: ImportSource[] }>("/mcp/import/sources")
      .then((res) => {
        if (live) setFetched(res.sources ?? []);
      })
      .catch((err) => {
        if (live) setError(errorText(err));
      });
    return () => {
      live = false;
    };
  }, [controlled]);

  const sources = controlled ? given : fetched;
  // Apps with tools lead; a file that could not be read keeps its own quiet
  // line (it names what went wrong); the empty places fold into one line.
  const found = (sources ?? []).filter((s) => s.readable && (s.servers ?? []).length > 0);
  const broken = (sources ?? []).filter((s) => !s.readable && !!s.note);
  const empty = (sources ?? []).filter(
    (s) => (s.readable && (s.servers ?? []).length === 0) || (!s.readable && !s.note),
  );

  return (
    <div data-testid="add-pack-sources-door">
      <p className="mb-2 text-[13px] text-zinc-500">
        The tools your other apps already use, read from their settings on this PC. Tick the ones
        you want here; nothing is added until you press Add.
      </p>
      {error ? (
        <ErrorNote>{error}</ErrorNote>
      ) : sources === null ? (
        <LoaderInline label="Reading your other apps" />
      ) : sources.length === 0 ? (
        <p className="text-[12px] text-zinc-500">No other apps with tools were found.</p>
      ) : (
        <div className="space-y-2">
          {found.length === 0 && (
            <p className="text-[12px] text-zinc-500" data-testid="import-nothing-found">
              None of your other apps lists a tool yet.
            </p>
          )}
          {found.map((s) => (
            <SourceGroup key={s.source} source={s} onAdded={onAdded} rowsHere={rowsHere} />
          ))}
          {broken.map((s) => (
            <SourceGroup key={s.source} source={s} onAdded={onAdded} rowsHere={rowsHere} />
          ))}
          <LookedIn sources={empty} />
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------- http door */

interface HeaderRow {
  id: number;
  name: string;
  value: string;
}

/** The server type: read off the address unless the user picked one. */
type TransportPick = "auto" | "http" | "sse";

function HttpDoor({ onAdded }: { onAdded: () => void }) {
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [pick, setPick] = useState<TransportPick>("auto");
  const [headers, setHeaders] = useState<HeaderRow[]>([]);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  // v1.351.0 (G08): saved, but it loaded no tools: the warn note, not ok.
  const [warn, setWarn] = useState<string | null>(null);
  const nextId = useState(() => ({ n: 1 }))[0];

  const derived = nameFromUrl(url);
  const finalName = safeName(name) || derived;
  // v1.354.0 (G49): an address ending in /sse is SSE, anything else HTTP.
  const transport: "http" | "sse" = pick === "auto" ? transportFromUrl(url) : pick;

  async function add() {
    if (!url.trim() || !finalName || busy) return;
    setBusy(true);
    setError(null);
    setOk(null);
    setWarn(null);
    try {
      const res = await post<{ name: string; tools_loaded: number; note?: string | null }>(
        "/mcp/servers",
        httpServerBody({ name: finalName, url, transport, headers, token }),
      );
      if (packDidNotStart(res)) {
        setWarn(addedButNotStarted(res.note, `"${res.name}"`));
      } else {
        setOk(
          res.note
            ? `"${res.name}" added. ${res.note}`
            : `"${res.name}" added with ${res.tools_loaded} tool${res.tools_loaded === 1 ? "" : "s"} ready. ${USE_IN_CHAT}`,
        );
      }
      setUrl("");
      setName("");
      setPick("auto");
      setHeaders([]);
      setToken("");
      onAdded();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div data-testid="add-pack-http-door">
      <p className="mb-2 text-[13px] text-zinc-500">
        A server that is already running somewhere, reached by its address. A token or a header that
        looks like a key is kept in the encrypted vault.
      </p>
      <input
        data-testid="add-pack-url"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        placeholder="https://host/mcp"
        className="field font-mono text-[12px]"
        aria-label="Server URL"
      />
      <label className="mt-2 block">
        <span className="mb-1 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">Name (optional)</span>
        <input
          data-testid="add-pack-http-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={derived || "my-server"}
          className="field px-2 py-1 font-mono text-[12px]"
        />
      </label>
      {/* v1.354.0 (G49): the type, headers and token are for the servers
          that need them; most need only the address. */}
      <details className="group mt-2" data-testid="add-pack-http-more">
        <summary className={FOLD_SUMMARY}>
          <ChevronRight size={12} className={FOLD_CHEVRON} aria-hidden />
          More options
        </summary>
        <div className="mt-2 space-y-2 pl-4">
          <label className="block">
            <span className="mb-1 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">Server type</span>
            <select
              data-testid="add-pack-http-transport"
              value={pick}
              onChange={(e) => {
                const v = e.target.value;
                setPick(v === "sse" ? "sse" : v === "http" ? "http" : "auto");
              }}
              className="field sm:w-72"
              aria-label="Server type"
            >
              <option value="auto">Automatic (read from the address)</option>
              <option value="http">HTTP</option>
              <option value="sse">SSE (older servers)</option>
            </select>
          </label>
          <div>
            <span className="mb-1 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">Headers (optional)</span>
            <div className="space-y-1">
              {headers.map((h) => (
                <div key={h.id} className="flex items-center gap-2">
                  <input
                    data-testid={`add-pack-header-name-${h.id}`}
                    value={h.name}
                    onChange={(e) =>
                      setHeaders((rows) => rows.map((r) => (r.id === h.id ? { ...r, name: e.target.value } : r)))
                    }
                    placeholder="Header name"
                    aria-label="Header name"
                    className="field w-44 px-2 py-1 font-mono text-[12px]"
                  />
                  <input
                    data-testid={`add-pack-header-value-${h.id}`}
                    type="password"
                    autoComplete="off"
                    value={h.value}
                    onChange={(e) =>
                      setHeaders((rows) => rows.map((r) => (r.id === h.id ? { ...r, value: e.target.value } : r)))
                    }
                    placeholder="Value"
                    aria-label="Header value"
                    className="field flex-1 px-2 py-1 font-mono text-[12px]"
                  />
                  <button
                    type="button"
                    onClick={() => setHeaders((rows) => rows.filter((r) => r.id !== h.id))}
                    aria-label="Remove header"
                    className="text-zinc-500 hover:text-zinc-300"
                  >
                    <X size={14} />
                  </button>
                </div>
              ))}
            </div>
            <button
              type="button"
              data-testid="add-pack-header-add"
              onClick={() => setHeaders((rows) => [...rows, { id: nextId.n++, name: "", value: "" }])}
              className="mt-1 text-[12px] font-medium text-zinc-400 hover:text-zinc-200"
            >
              + Add a header
            </button>
          </div>
          <label className="block">
            <span className="mb-1 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">Access token (optional)</span>
            <input
              data-testid="add-pack-http-token"
              type="password"
              autoComplete="off"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="Sent with every request"
              className="field px-2 py-1 font-mono text-[12px]"
            />
          </label>
        </div>
      </details>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button
          type="button"
          data-testid="add-pack-http-add"
          onClick={() => void add()}
          disabled={!url.trim() || !finalName || busy}
          className="btn-accent"
        >
          {busy ? (
            <LoaderInline label="Adding" />
          ) : (
            <>
              <Plus size={14} /> Add this server
            </>
          )}
        </button>
        {finalName && <span className="font-mono text-[11px] text-zinc-500">as {finalName}</span>}
      </div>
      {error && (
        <div className="mt-2">
          <ErrorNote>{error}</ErrorNote>
        </div>
      )}
      {ok && (
        <div className="mt-2" data-testid="add-pack-http-ok">
          <SuccessNote>{ok}</SuccessNote>
        </div>
      )}
      {warn && (
        <div className="mt-2" data-testid="add-pack-http-warn">
          <WarnNote>{warn}</WarnNote>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ area */

/** The Tools page's add area: its pack rows are on this page (`rowsHere`). */
export function AddPack({ onAdded }: { onAdded: () => void }) {
  const [door, setDoor] = useState<Door>("sources");
  return (
    <div data-testid="add-pack">
      <div role="tablist" aria-label="Ways to add a tool" className="mb-3 flex flex-wrap gap-1">
        {DOORS.map((d) => (
          <button
            key={d.id}
            type="button"
            role="tab"
            aria-selected={door === d.id}
            data-testid={`add-pack-door-${d.id}`}
            onClick={() => setDoor(d.id)}
            className={`inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-[12px] font-medium transition-colors ${
              door === d.id
                ? "border-accent/40 bg-accent/[0.12] text-accent-soft"
                : "border-white/10 text-zinc-400 hover:border-white/20 hover:text-zinc-200"
            }`}
          >
            {d.icon}
            {d.label}
          </button>
        ))}
      </div>
      {door === "sources" && <SourcesDoor onAdded={onAdded} rowsHere />}
      {door === "paste" && <PasteDoor onAdded={onAdded} rowsHere />}
      {door === "http" && <HttpDoor onAdded={onAdded} />}
    </div>
  );
}

"use client";

/**
 * v1.343.0 ("add a pack in one paste"), lane K1: the "Add a pack" area of the
 * Tools page. Three doors, ONE preview list:
 *
 *   (a) Paste a config: one box that takes anything (a README's
 *       {"mcpServers": ...} block, a Codex TOML, an npx line, a URL);
 *       POST /mcp/import/preview renders the rows, "Add selected" posts
 *       POST /mcp/import/apply with the names and the typed overrides.
 *   (b) Import from your other apps: GET /mcp/import/sources, one group per
 *       source (label + file path); an unreadable source is one quiet line.
 *   (c) HTTP server: URL, optional header rows, optional bearer token ->
 *       POST /mcp/servers with transport http (sse when chosen).
 *
 * VALUES NEVER RENDER. A preview row carries key and header NAMES only; the
 * override boxes are password fields whose text goes to the apply route and
 * nowhere else. The text the user pasted is re-sent to the apply route as
 * is (the daemon re-parses it; the browser never holds a parsed value).
 */

import { useEffect, useState, type ReactNode } from "react";
import { Check, ClipboardPaste, FolderInput, Globe, Plus, TriangleAlert, X } from "lucide-react";
import { ApiError, get, post } from "@/lib/api";
import { Badge, ErrorNote, LoaderInline, SuccessNote, WarnNote } from "@/components/ui";
import { addedButNotStarted, packDidNotStart } from "@/lib/packStartWords";
import { StatusChip } from "./chips";
import {
  httpServerBody,
  nameFromUrl,
  rowLaunch,
  rowStatus,
  safeName,
  type ApplyResult,
  type ImportSource,
  type PreviewResult,
  type PreviewRow,
} from "@/lib/mcpImport";

type Door = "paste" | "sources" | "http";

const DOORS: { id: Door; label: string; icon: ReactNode }[] = [
  { id: "paste", label: "Paste a config", icon: <ClipboardPaste size={13} aria-hidden /> },
  { id: "sources", label: "Import from your other apps", icon: <FolderInput size={13} aria-hidden /> },
  { id: "http", label: "HTTP server", icon: <Globe size={13} aria-hidden /> },
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

/* ---------------------------------------------------------------- rows */

/** The one preview list every door ends in. */
function PreviewList({
  rows,
  selected,
  onToggle,
  overrides,
  onOverride,
  showOrigin = false,
}: {
  rows: PreviewRow[];
  selected: Set<string>;
  onToggle: (name: string) => void;
  overrides: Overrides;
  onOverride: (name: string, key: string, value: string) => void;
  showOrigin?: boolean;
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
        return (
          <li
            key={`${row.origin ?? ""}:${row.name}`}
            data-testid={`import-row-${row.name}`}
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
              <Badge value={launcher} tone="slate" variant="calm" keepCase data-testid={`import-launcher-${row.name}`} />
              {st.status === "added" ? (
                <Badge value="already added" tone="slate" variant="calm" keepCase data-testid={`import-exists-${row.name}`} />
              ) : st.status === "available" ? null : (
                <StatusChip status={st.status} needs={st.needs} />
              )}
              {secretKeys.length > 0 && (
                <Badge value={`${secretKeys.length} in vault`} tone="violet" variant="calm" keepCase />
              )}
            </div>
            <div className="mt-1 overflow-x-auto">
              <code className="whitespace-pre font-mono text-[11px] text-zinc-500">{rowLaunch(row)}</code>
            </div>
            {showOrigin && row.origin && row.origin !== "paste" && (
              <p className="mt-1 truncate text-[11px] text-zinc-600" title={row.origin}>
                from {row.origin}
              </p>
            )}
            {row.launcher_note && (
              <p className="mt-1 text-[12px] text-tone-warn" data-testid={`import-note-${row.name}`}>
                {row.launcher_note}
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
          </li>
        );
      })}
    </ul>
  );
}

/** What apply answered, row by row. v1.351.0 (G08): a pack that loaded NO
 *  tools was saved but did not start, so its row carries the warning triangle
 *  and "Added, but it didn't start: <why>. Open its row below to retry." in
 *  the warn tone, never the green check. */
function ApplyOutcome({ result }: { result: ApplyResult }) {
  return (
    <div className="mt-3 space-y-1" data-testid="import-outcome">
      {result.added.map((a) => {
        const notStarted = packDidNotStart(a);
        return (
          <div
            key={`added:${a.name}`}
            data-testid={`import-result-${a.name}`}
            data-outcome={notStarted ? "not-started" : "ok"}
            className="flex flex-wrap items-center gap-2 text-[12px] text-zinc-300"
          >
            {notStarted ? (
              <TriangleAlert size={12} className="text-tone-warn" aria-hidden data-testid={`import-warn-${a.name}`} />
            ) : (
              <Check size={12} className="text-tone-success" aria-hidden />
            )}
            <span className="font-mono font-semibold text-zinc-100">{a.name}</span>
            {notStarted ? (
              <span className="text-tone-warn">{addedButNotStarted(a.note)}</span>
            ) : a.note ? (
              <span className="text-tone-warn">{a.note}</span>
            ) : (
              <span>
                {a.tools_loaded} tool{a.tools_loaded === 1 ? "" : "s"} ready
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
    </div>
  );
}

/* ------------------------------------------------------------- selection */

function pickable(rows: PreviewRow[]): Set<string> {
  return new Set(rows.filter((r) => !r.exists).map((r) => r.name));
}

function useSelection(initial: PreviewRow[] = []) {
  const [selected, setSelected] = useState<Set<string>>(() => pickable(initial));
  const [overrides, setOverrides] = useState<Overrides>({});
  const seed = (rows: PreviewRow[]) => {
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

function PasteDoor({ onAdded }: { onAdded: () => void }) {
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

  const rows = preview?.servers ?? [];
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
      {result && <ApplyOutcome result={result} />}
    </div>
  );
}

/* ---------------------------------------------------------- sources door */

function SourceGroup({ source, onAdded }: { source: ImportSource; onAdded: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ApplyResult | null>(null);
  const rows = source.servers ?? [];
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
        <p className="mt-1 text-[12px] text-zinc-500">No servers in this file.</p>
      ) : (
        <>
          <PreviewList
            rows={rows}
            selected={sel.selected}
            onToggle={sel.toggle}
            overrides={sel.overrides}
            onOverride={sel.setOverride}
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
      {result && <ApplyOutcome result={result} />}
    </div>
  );
}

/**
 * The "Import from your other apps" door. v1.353.0 (G42): exported so the
 * Directory (Settings › Connections › Apps & tools) opens the SAME door under
 * its discovery line. `sources` given (the list the caller already read;
 * `null` while it loads) = no second read here, and a re-read after an
 * import flows straight in; left out = the door reads it itself, as on the
 * Tools page.
 */
export function SourcesDoor({
  onAdded,
  sources: given,
}: {
  onAdded: () => void;
  sources?: ImportSource[] | null;
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

  return (
    <div data-testid="add-pack-sources-door">
      <p className="mb-2 text-[13px] text-zinc-500">
        The servers your other apps already use, read from their config files on this PC. Pick the
        ones you want here.
      </p>
      {error ? (
        <ErrorNote>{error}</ErrorNote>
      ) : sources === null ? (
        <LoaderInline label="Reading your other apps" />
      ) : sources.length === 0 ? (
        <p className="text-[12px] text-zinc-500">No other apps with servers were found.</p>
      ) : (
        <div className="space-y-2">
          {sources.map((s) => (
            <SourceGroup key={s.source} source={s} onAdded={onAdded} />
          ))}
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

function HttpDoor({ onAdded }: { onAdded: () => void }) {
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [transport, setTransport] = useState<"http" | "sse">("http");
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
            : `"${res.name}" added with ${res.tools_loaded} tool${res.tools_loaded === 1 ? "" : "s"} ready.`,
        );
      }
      setUrl("");
      setName("");
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
        A server that is already running somewhere, reached by URL. A token or a header that looks
        like a key is kept in the encrypted vault.
      </p>
      <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
        <input
          data-testid="add-pack-url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://host/mcp"
          className="field font-mono text-[12px]"
          aria-label="Server URL"
        />
        <select
          data-testid="add-pack-http-transport"
          value={transport}
          onChange={(e) => setTransport(e.target.value === "sse" ? "sse" : "http")}
          className="field sm:w-40"
          aria-label="Server type"
        >
          <option value="http">Streamable HTTP</option>
          <option value="sse">SSE</option>
        </select>
      </div>
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
      <div className="mt-2">
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
      <label className="mt-2 block">
        <span className="mb-1 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">Bearer token (optional)</span>
        <input
          data-testid="add-pack-http-token"
          type="password"
          autoComplete="off"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="Sent as Authorization: Bearer ..."
          className="field px-2 py-1 font-mono text-[12px]"
        />
      </label>
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

export function AddPack({ onAdded }: { onAdded: () => void }) {
  const [door, setDoor] = useState<Door>("paste");
  return (
    <div data-testid="add-pack">
      <div role="tablist" aria-label="Ways to add a pack" className="mb-3 flex flex-wrap gap-1">
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
      {door === "paste" && <PasteDoor onAdded={onAdded} />}
      {door === "sources" && <SourcesDoor onAdded={onAdded} />}
      {door === "http" && <HttpDoor onAdded={onAdded} />}
    </div>
  );
}

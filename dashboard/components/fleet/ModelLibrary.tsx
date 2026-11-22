"use client";

/**
 * MODEL SERVERS › MODELS — the model library (next leaps 43-a, R1.7; idea 43,
 * in LM Studio's style). A tab on the Model servers page, never a page of its
 * own (G2).
 *
 * - The line on top says what THIS PC has, from what it read ("This PC has
 *   64 GB of memory and 412 GB free on the drive Ollama uses. 7 of the 13
 *   models below fit."). A server on another computer says that its room is
 *   not known; nothing is guessed.
 * - Each row: name, what it is good at, download size, memory it needs, and
 *   its mark (Fits this PC (64 GB) / Tight / Too big / Fit not known).
 * - Download first reads the exact size and the free space after (one
 *   press), then Start download (the one main button while that row is
 *   open, G4); a download that would leave less than a tenth of the drive
 *   free is refused in words with no Start.
 * - Progress and Cancel come from the daemon's events while the tab is open;
 *   nothing is polled (G7).
 * - The search box asks Hugging Face ONLY for what you type there (D-43.1),
 *   and says so beside the box.
 *
 * At 390 px every row stacks: its words first, then its marks, then its
 * press; nothing sits side by side that cannot wrap (G11).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Download as DownloadIcon, HardDrive, Loader2, Search } from "lucide-react";
import { Badge, Card, Empty, ErrorNote, SkeletonRows } from "@/components/ui";
import { DownloadProgress } from "@/components/fleet/DownloadProgress";
import { useEvents } from "@/lib/useEvents";
import {
  DOWNLOAD_EVENT_TYPES,
  fitTone,
  foldDownload,
  readDownloads,
  readLibrary,
  readSize,
  startDownload,
  type Download,
  type DownloadSize,
  type LibraryModel,
  type LibraryView,
} from "@/lib/modelLibrary";

/** How long the search box waits after the last key before it asks. */
export const SEARCH_WAIT_MS = 600;
/** Fewer letters than this ask nothing. */
export const SEARCH_MIN_CHARS = 2;

function errorWords(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function ModelRow({
  m,
  node,
  busyElsewhere,
  onStarted,
}: {
  m: LibraryModel;
  node: string;
  busyElsewhere: boolean;
  onStarted: (d: Download) => void;
}) {
  const [size, setSize] = useState<DownloadSize | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function askSize() {
    setBusy(true);
    setErr("");
    try {
      setSize(await readSize(node, m.model));
    } catch (e) {
      setErr(errorWords(e));
    } finally {
      setBusy(false);
    }
  }

  async function start() {
    setBusy(true);
    setErr("");
    try {
      const d = await startDownload(node, m.model);
      setSize(null);
      if (d) onStarted(d);
    } catch (e) {
      setErr(errorWords(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li data-testid={`library-row-${m.id}`} className="space-y-2 py-3 first:pt-0 last:pb-0">
      <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-0.5">
          <p className="break-words text-[13px] font-medium text-zinc-100">{m.name}</p>
          {m.good_at && <p className="break-words text-xs leading-relaxed text-zinc-400">{m.good_at}</p>}
          <p className="break-words text-[11px] text-zinc-500">
            <span className="font-mono">{m.model}</span>
            {" · "}download {m.size_words}
            {" · "}memory {m.memory_words}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <Badge
            variant="calm"
            keepCase
            tone={fitTone(m.fit)}
            value={m.fit_words}
            data-testid={`library-fit-${m.id}`}
            data-status={m.fit}
          />
          {m.installed ? (
            <span data-testid={`library-installed-${m.id}`} className="text-[11px] text-zinc-500">
              Installed
            </span>
          ) : m.can_download && !size ? (
            <button
              type="button"
              data-testid={`library-download-${m.id}`}
              onClick={() => void askSize()}
              disabled={busy || busyElsewhere}
              title={busyElsewhere ? "This server is downloading another model. One at a time." : undefined}
              className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-zinc-300 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy ? <Loader2 size={12} className="animate-spin" aria-hidden /> : <DownloadIcon size={12} aria-hidden />}
              Download
            </button>
          ) : null}
        </div>
      </div>
      {size && (
        <div
          data-testid={`library-size-${m.id}`}
          className="space-y-2 rounded-lg border border-white/[0.06] bg-white/[0.02] p-3 text-xs text-zinc-300"
        >
          <p className="break-words">
            {size.name} from {size.source}:{" "}
            <span className="font-medium text-zinc-100">{size.size_words}</span>
            {size.size_from === "measured" ? " (measured just now)" : size.size_from === "about" ? " (the list's own size)" : ""}
            {" · "}
            {size.free_after_words}.
          </p>
          {size.refusal ? (
            <p data-testid={`library-refusal-${m.id}`} className="break-words text-tone-warn">
              {size.refusal}
            </p>
          ) : (
            <p className="break-words text-zinc-500">{size.partial_words.replace(/^Ollama keeps/, "If you cancel, Ollama keeps")}</p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            {size.can_start && (
              <button
                type="button"
                data-testid={`library-start-${m.id}`}
                onClick={() => void start()}
                disabled={busy}
                className="btn-accent btn-sm inline-flex items-center gap-1.5"
              >
                {busy ? <Loader2 size={12} className="animate-spin" aria-hidden /> : <DownloadIcon size={12} aria-hidden />}
                Start download
              </button>
            )}
            <button
              type="button"
              data-testid={`library-not-now-${m.id}`}
              onClick={() => setSize(null)}
              className="rounded-md px-2 py-1 text-xs text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
            >
              Not now
            </button>
          </div>
        </div>
      )}
      {err && (
        <p role="alert" className="break-words text-xs text-tone-danger">
          {err}
        </p>
      )}
    </li>
  );
}

export function ModelLibrary() {
  const [node, setNode] = useState("");
  const [view, setView] = useState<LibraryView | null>(null);
  const [loadErr, setLoadErr] = useState("");
  const [words, setWords] = useState("");
  const [found, setFound] = useState<LibraryView["search"] | null>(null);
  const [searching, setSearching] = useState(false);
  const [downloads, setDownloads] = useState<Download[]>([]);
  const asked = useRef(0);

  const load = useCallback(async (which: string) => {
    setLoadErr("");
    try {
      setView(await readLibrary(which));
    } catch (e) {
      setLoadErr(errorWords(e));
    }
  }, []);

  useEffect(() => {
    void load(node);
  }, [load, node]);

  useEffect(() => {
    let live = true;
    readDownloads()
      .then((list) => live && setDownloads(list))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);

  // Progress and the end, as the daemon says them (no polling).
  const { events } = useEvents(40, { types: DOWNLOAD_EVENT_TYPES });
  const lastSeen = useRef<string>("");
  useEffect(() => {
    if (!events.length) return;
    const fresh: typeof events = [];
    for (const e of events) {
      if (e.id === lastSeen.current) break;
      fresh.push(e);
    }
    if (!fresh.length) return;
    lastSeen.current = events[0].id ?? "";
    let ended = false;
    setDownloads((cur) => {
      let next = cur;
      for (const e of fresh.slice().reverse()) {
        next = foldDownload(next, e.payload);
        if (e.type === "model.download.ended") ended = true;
      }
      return next;
    });
    if (ended) void load(node);
  }, [events, load, node]);

  // The search box: Hugging Face only for typed words, after a pause, for
  // the server on show (its rows' Download goes there).
  const shownId = view?.server?.id ?? node;
  useEffect(() => {
    const q = words.trim();
    if (q.length < SEARCH_MIN_CHARS) {
      setFound(null);
      setSearching(false);
      return;
    }
    const mine = ++asked.current;
    setSearching(true);
    const t = setTimeout(() => {
      readLibrary(shownId, q)
        .then((v) => mine === asked.current && setFound(v.search))
        .catch((e) => mine === asked.current && setFound({ words: q, models: [], why: errorWords(e) }))
        .finally(() => mine === asked.current && setSearching(false));
    }, SEARCH_WAIT_MS);
    return () => clearTimeout(t);
  }, [words, shownId]);

  const server = view?.server ?? null;
  const running = useMemo(() => downloads.filter((d) => d.state === "downloading"), [downloads]);
  const recent = useMemo(() => downloads.filter((d) => d.state !== "downloading").slice(0, 3), [downloads]);
  const busyHere = !!server && running.some((d) => d.node_id === server.id);
  const started = useCallback((d: Download) => setDownloads((cur) => foldDownload(cur, d)), []);
  const changed = useCallback((d: Download) => setDownloads((cur) => foldDownload(cur, d)), []);

  if (loadErr && !view) {
    return (
      <Card title="Models" icon={<HardDrive size={15} />}>
        <ErrorNote>{loadErr}</ErrorNote>
      </Card>
    );
  }
  if (!view) {
    return (
      <Card title="Models" icon={<HardDrive size={15} />}>
        <SkeletonRows rows={5} />
      </Card>
    );
  }

  return (
    <div data-testid="model-library" className="space-y-4">
      <Card
        title="Models"
        icon={<HardDrive size={15} />}
        right={
          view.servers.length > 1 ? (
            <label className="flex min-w-0 items-center gap-2 text-xs text-zinc-500">
              <span className="shrink-0">Model server</span>
              <select
                data-testid="library-server"
                value={server?.id ?? ""}
                onChange={(e) => setNode(e.target.value)}
                className="min-w-0 max-w-[12rem] rounded-md border border-white/10 bg-transparent px-2 py-1 text-xs text-zinc-200"
              >
                {view.servers.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.label}
                  </option>
                ))}
              </select>
            </label>
          ) : undefined
        }
      >
        <div className="space-y-3">
          {view.error && <ErrorNote>{view.error}</ErrorNote>}
          <p data-testid="library-summary" className="break-words text-xs leading-relaxed text-zinc-400">
            {view.summary}
          </p>
          {server && !server.downloads && server.download_why && (
            <p data-testid="library-no-download" className="break-words text-xs leading-relaxed text-zinc-500">
              {server.download_why}
            </p>
          )}
          {(running.length > 0 || recent.length > 0) && (
            <div data-testid="library-downloads" className="space-y-3 border-t hairline pt-3">
              {[...running, ...recent].map((d) => (
                <DownloadProgress key={d.id} download={d} onChange={changed} testId={`library-download-row-${d.id}`} />
              ))}
            </div>
          )}
        </div>
      </Card>

      {view.servers.length === 0 && view.models.length === 0 ? (
        <Card title="The list" icon={<DownloadIcon size={15} />}>
          <Empty icon={<HardDrive size={26} />} title="No model server yet">
            {view.summary}
          </Empty>
        </Card>
      ) : (
        <>
          {/* With no model server yet the list is still shown, marked
              against this PC, so you can see what fits before installing
              one; there is nowhere to download to, so no search and no
              Download. */}
          {view.servers.length > 0 && (
            <Card title="Search Hugging Face" icon={<Search size={15} />}>
              <div className="space-y-2">
                <input
                  data-testid="library-search"
                  type="search"
                  value={words}
                  onChange={(e) => setWords(e.target.value)}
                  placeholder="Find more models, for example qwen coder"
                  maxLength={80}
                  className="w-full min-w-0 rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2 text-sm text-zinc-200 placeholder:text-zinc-600"
                />
                <p className="text-[11px] leading-relaxed text-zinc-500">
                  Only the words you type here are sent to Hugging Face, and only after you type them.
                </p>
                {searching && (
                  <p className="flex items-center gap-1.5 text-[11px] text-zinc-500">
                    <Loader2 size={11} className="animate-spin" aria-hidden /> Searching…
                  </p>
                )}
                {found && !searching && (
                  found.models.length ? (
                    <ul data-testid="library-found" className="divide-y divide-white/[0.05]">
                      {found.models.map((m) => (
                        <ModelRow key={m.id} m={m} node={server?.id ?? ""} busyElsewhere={busyHere} onStarted={started} />
                      ))}
                    </ul>
                  ) : (
                    <p data-testid="library-found-none" className="break-words text-xs text-zinc-500">
                      {found.why || `Hugging Face lists nothing for “${found.words}” that a model server here can run.`}
                    </p>
                  )
                )}
              </div>
            </Card>
          )}

          <Card title="The list" icon={<DownloadIcon size={15} />}>
            <ul data-testid="library-list" className="divide-y divide-white/[0.05]">
              {view.models.map((m) => (
                <ModelRow key={m.id} m={m} node={server?.id ?? ""} busyElsewhere={busyHere} onStarted={started} />
              ))}
            </ul>
            {view.checked && (
              <p className="mt-3 text-[11px] text-zinc-600">
                This list ships with Iron Jarvis and was checked on {view.checked}. The size read just before a
                download is the one that counts.
              </p>
            )}
          </Card>
        </>
      )}
    </div>
  );
}

export default ModelLibrary;

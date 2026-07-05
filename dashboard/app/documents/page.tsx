"use client";

import { useEffect, useRef, useState } from "react";
import {
  FileText,
  FileSpreadsheet,
  FileType,
  Presentation,
  FileCode,
  File,
  FileDown,
  FileUp,
  FolderOpen,
  Upload,
  Sparkles,
  Archive,
  Brain,
  Lightbulb,
  type LucideIcon,
} from "lucide-react";
import { get, post, ApiError } from "@/lib/api";
import type { DocumentRead, DocumentWriteResult } from "@/lib/types";
import {
  Card,
  Badge,
  Empty,
  OfflineHint,
  ErrorNote,
  SuccessNote,
  LoaderInline,
  type Tone,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { VoiceInput, appendDictation } from "@/components/VoiceInput";
import { FilePickerModal } from "@/components/FilePickerModal";

/* -------------------------------------------------------------------------- */
/*  File-type detection (from the path/filename suffix)                        */
/* -------------------------------------------------------------------------- */

interface DocType {
  label: string;
  tone: Tone;
  Icon: LucideIcon;
}

const EXT_TYPE: Record<string, DocType> = {
  doc: { label: "Word", tone: "cyan", Icon: FileText },
  docx: { label: "Word", tone: "cyan", Icon: FileText },
  xls: { label: "Excel", tone: "green", Icon: FileSpreadsheet },
  xlsx: { label: "Excel", tone: "green", Icon: FileSpreadsheet },
  pdf: { label: "PDF", tone: "red", Icon: FileType },
  ppt: { label: "PowerPoint", tone: "amber", Icon: Presentation },
  pptx: { label: "PowerPoint", tone: "amber", Icon: Presentation },
  md: { label: "Markdown", tone: "violet", Icon: FileCode },
  csv: { label: "CSV", tone: "green", Icon: FileSpreadsheet },
  txt: { label: "Text", tone: "slate", Icon: FileText },
  json: { label: "JSON", tone: "slate", Icon: FileCode },
  html: { label: "HTML", tone: "slate", Icon: FileCode },
  yaml: { label: "YAML", tone: "slate", Icon: FileCode },
  yml: { label: "YAML", tone: "slate", Icon: FileCode },
  log: { label: "Log", tone: "slate", Icon: FileText },
};

function docTypeFor(name: string): DocType {
  const trimmed = name.trim();
  const ext =
    trimmed.includes(".") && !trimmed.endsWith(".")
      ? trimmed.split(".").pop()!.toLowerCase()
      : "";
  return (
    EXT_TYPE[ext] ?? {
      label: ext ? ext.toUpperCase() : "Text",
      tone: "slate" as Tone,
      Icon: File,
    }
  );
}

const SUPPORTED_CREATE =
  "Word (.docx), Excel (.xlsx), PowerPoint (.pptx), PDF (.pdf), CSV (.csv), Markdown (.md), Text (.txt)";

/* -------------------------------------------------------------------------- */
/*  API payload shapes local to this page                                      */
/* -------------------------------------------------------------------------- */

/** POST /documents/upload response (same contract the chat composer uses). */
interface UploadResult {
  path: string;
  name?: string;
  bytes?: number;
}

/** POST /documents/enhance response — an AI-polished draft for review. */
interface EnhanceResult {
  filename: string;
  content: string;
  notes: string;
}

/* -------------------------------------------------------------------------- */
/*  Helpers                                                                    */
/* -------------------------------------------------------------------------- */

/** Read a File as raw base64 (FileReader gives a data: URL — strip the prefix). */
function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("could not read file"));
    reader.onload = () => {
      const res = String(reader.result);
      const comma = res.indexOf(",");
      resolve(comma >= 0 ? res.slice(comma + 1) : res);
    };
    reader.readAsDataURL(file);
  });
}

/** Last path segment (handles both / and \ separators). */
function baseName(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

/* -------------------------------------------------------------------------- */
/*  Save-to-memory row                                                         */
/* -------------------------------------------------------------------------- */

const MEMORY_CAP = 8000; // /ltm/append + /memory text cap
const LESSON_CAP = 500; // /lessons excerpt cap

type MemoryTarget = "ltm" | "working" | "lessons";

interface SaveTargetDef {
  key: MemoryTarget;
  label: string;
  Icon: LucideIcon;
}

const SAVE_TARGETS: SaveTargetDef[] = [
  { key: "ltm", label: "Long-term memory", Icon: Archive },
  { key: "working", label: "Working memory", Icon: Brain },
  { key: "lessons", label: "What I've learned", Icon: Lightbulb },
];

/**
 * "Save to → …" row shown wherever document text is on screen. Each button
 * pushes the text into one of the three memory systems.
 */
function SaveToMemoryRow({ filename, text }: { filename: string; text: string }) {
  const [busy, setBusy] = useState<MemoryTarget | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function save(target: SaveTargetDef) {
    setBusy(target.key);
    setSaved(null);
    setError(null);
    try {
      if (target.key === "ltm") {
        await post<unknown>("/ltm/append", {
          title: filename,
          content: text.slice(0, MEMORY_CAP),
        });
      } else if (target.key === "working") {
        await post<unknown>("/memory", {
          layer: "user",
          key: filename,
          text: text.slice(0, MEMORY_CAP),
        });
      } else {
        await post<unknown>("/lessons", {
          text: `From ${filename}: ${text.slice(0, LESSON_CAP)}`,
        });
      }
      setSaved(target.label);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] uppercase tracking-[0.1em] text-zinc-500">
          Save to →
        </span>
        {SAVE_TARGETS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => void save(t)}
            disabled={busy !== null}
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1 text-xs font-medium text-zinc-300 transition-colors hover:border-accent/40 hover:text-accent-soft disabled:opacity-50"
          >
            {busy === t.key ? (
              <LoaderInline label="Saving…" />
            ) : (
              <>
                <t.Icon size={13} /> {t.label}
              </>
            )}
          </button>
        ))}
      </div>
      {saved && <SuccessNote>Saved to {saved}.</SuccessNote>}
      {error && <ErrorNote>{error}</ErrorNote>}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Page                                                                       */
/* -------------------------------------------------------------------------- */

export default function DocumentsPage() {
  /* ---- Read / extract --------------------------------------------------- */
  const [readPath, setReadPath] = useState("");
  const [readText, setReadText] = useState<string | null>(null);
  const [readDoneType, setReadDoneType] = useState<DocType | null>(null);
  const [readName, setReadName] = useState<string | null>(null);
  const [readBusy, setReadBusy] = useState(false);
  const [readError, setReadError] = useState<string | null>(null);
  const [readOffline, setReadOffline] = useState(false);
  const [browseOpen, setBrowseOpen] = useState(false);

  /* ---- Drop zone (upload → read) ----------------------------------------- */
  const [dragOver, setDragOver] = useState(false);
  const [uploadBusy, setUploadBusy] = useState(false);
  const dropFileRef = useRef<HTMLInputElement>(null);

  /* ---- Create document -------------------------------------------------- */
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const [writeBusy, setWriteBusy] = useState(false);
  const [writeError, setWriteError] = useState<string | null>(null);
  const [writeOk, setWriteOk] = useState<DocumentWriteResult | null>(null);
  const [writeOffline, setWriteOffline] = useState(false);

  /* ---- AI enhance -------------------------------------------------------- */
  const [enhanceBusy, setEnhanceBusy] = useState(false);
  const [enhanceError, setEnhanceError] = useState<string | null>(null);
  const [suggestion, setSuggestion] = useState<EnhanceResult | null>(null);

  const writeType = docTypeFor(name);
  const ReadIcon = (readDoneType ?? docTypeFor(readPath)).Icon;

  // Dropping a file OUTSIDE the drop zone must not navigate the tab away.
  useEffect(() => {
    const prevent = (e: DragEvent) => {
      if (Array.from(e.dataTransfer?.types ?? []).includes("Files"))
        e.preventDefault();
    };
    window.addEventListener("dragover", prevent);
    window.addEventListener("drop", prevent);
    return () => {
      window.removeEventListener("dragover", prevent);
      window.removeEventListener("drop", prevent);
    };
  }, []);

  async function runExtract(path: string) {
    setReadBusy(true);
    setReadError(null);
    setReadOffline(false);
    try {
      const data = await get<DocumentRead>(
        `/documents/read?path=${encodeURIComponent(path)}`,
      );
      setReadText(data.text ?? "");
      setReadDoneType(docTypeFor(data.path || path));
      setReadName(baseName(data.path || path));
    } catch (err) {
      if (err instanceof ApiError && err.status === 0) setReadOffline(true);
      else setReadError(err instanceof ApiError ? err.message : String(err));
      setReadText(null);
      setReadDoneType(null);
      setReadName(null);
    } finally {
      setReadBusy(false);
    }
  }

  function extract(e: React.FormEvent) {
    e.preventDefault();
    if (!readPath.trim()) return;
    void runExtract(readPath.trim());
  }

  /** Drop-zone flow: upload the local file, then read it straight away. */
  async function uploadAndRead(file: File) {
    if (uploadBusy || readBusy) return;
    setUploadBusy(true);
    setReadError(null);
    setReadOffline(false);
    try {
      const content_b64 = await readAsBase64(file);
      const res = await post<UploadResult>("/documents/upload", {
        filename: file.name,
        content_b64,
      });
      setReadPath(res.path);
      await runExtract(res.path);
    } catch (err) {
      if (err instanceof ApiError && err.status === 0) setReadOffline(true);
      else setReadError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setUploadBusy(false);
    }
  }

  function onDropZoneDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) void uploadAndRead(file);
  }

  function onPickLocalFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-selecting the same file
    if (file) void uploadAndRead(file);
  }

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !content.trim()) return;
    setWriteBusy(true);
    setWriteError(null);
    setWriteOk(null);
    setWriteOffline(false);
    try {
      const res = await post<DocumentWriteResult>("/documents/write", {
        path: name.trim(),
        content,
      });
      setWriteOk(res);
    } catch (err) {
      if (err instanceof ApiError && err.status === 0) setWriteOffline(true);
      else setWriteError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setWriteBusy(false);
    }
  }

  /** Ask the daemon's AI for a polished draft — never applied without review. */
  async function enhance() {
    if (!name.trim() || !content.trim()) return;
    setEnhanceBusy(true);
    setEnhanceError(null);
    setSuggestion(null);
    setWriteOffline(false);
    try {
      const res = await post<EnhanceResult>("/documents/enhance", {
        filename: name.trim(),
        content,
      });
      setSuggestion(res);
    } catch (err) {
      if (err instanceof ApiError && err.status === 0) setWriteOffline(true);
      else setEnhanceError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setEnhanceBusy(false);
    }
  }

  function applySuggestion() {
    if (!suggestion) return;
    setName(suggestion.filename);
    setContent(suggestion.content);
    setSuggestion(null);
  }

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Documents"
          subtitle="Pull the text out of any PDF, Word, Excel, PowerPoint, CSV or Markdown file — or have Iron Jarvis create a real document for you. Dictate the contents with the mic."
        />
      </Reveal>

      {(readOffline || writeOffline) && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      <Reveal>
        <div className="grid gap-6 lg:grid-cols-2">
          {/* ---- Read / extract -------------------------------------------- */}
          <Card title="Read & extract" icon={<FileDown size={15} />}>
            <form onSubmit={extract} className="space-y-3.5">
              {/* Drop zone: local file → upload → auto-read */}
              <div
                role="button"
                tabIndex={0}
                aria-label="Drop a file here to read it, or browse for one"
                onClick={() => {
                  if (!uploadBusy && !readBusy) dropFileRef.current?.click();
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    if (!uploadBusy && !readBusy) dropFileRef.current?.click();
                  }
                }}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragOver(true);
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={onDropZoneDrop}
                className={`flex w-full cursor-pointer flex-col items-center justify-center rounded-xl border border-dashed px-4 py-6 text-center transition-all ${
                  dragOver
                    ? "border-accent/70 bg-accent/[0.07] ring-2 ring-accent/30"
                    : "border-white/15 bg-ink-900/40 hover:border-accent/40"
                } ${uploadBusy || readBusy ? "opacity-60" : ""}`}
              >
                <div className="pointer-events-none flex flex-col items-center gap-1.5">
                  {uploadBusy ? (
                    <span className="text-sm text-zinc-300">
                      <LoaderInline label="Uploading…" />
                    </span>
                  ) : (
                    <>
                      <Upload size={18} className="text-accent-soft/70" />
                      <div className="text-sm text-zinc-300">
                        Drop a file here to read it — or{" "}
                        <span className="font-medium text-accent-soft">
                          Browse
                        </span>
                      </div>
                      <div className="text-[11px] text-zinc-600">
                        Uploads to the daemon, then extracts the text
                        automatically.
                      </div>
                    </>
                  )}
                </div>
              </div>
              <input
                ref={dropFileRef}
                type="file"
                className="hidden"
                onChange={onPickLocalFile}
              />

              <div>
                <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                  File path
                </label>
                <div className="flex items-stretch gap-2">
                  <div className="relative flex-1">
                    <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-accent-soft/70">
                      <ReadIcon size={15} />
                    </span>
                    <input
                      value={readPath}
                      onChange={(e) => setReadPath(e.target.value)}
                      placeholder="C:\Users\you\report.pdf"
                      className="field pl-9 font-mono"
                    />
                  </div>
                  <button
                    type="button"
                    onClick={() => setBrowseOpen(true)}
                    title="Browse for a file"
                    className="btn-ghost shrink-0"
                  >
                    <FolderOpen size={14} /> Browse…
                  </button>
                  <button
                    type="submit"
                    disabled={readBusy || uploadBusy || !readPath.trim()}
                    className="btn-accent shrink-0"
                  >
                    {readBusy ? (
                      <LoaderInline label="Reading…" />
                    ) : (
                      <>
                        <FileDown size={14} /> Extract text
                      </>
                    )}
                  </button>
                </div>
                <div className="mt-1.5 text-[11px] text-zinc-600">
                  Absolute or relative path. Reads PDF, Word, Excel, PowerPoint,
                  CSV, Markdown and plain text.
                </div>
              </div>

              {readError && <ErrorNote>{readError}</ErrorNote>}

              {readText !== null && !readError && (
                <div className="space-y-3">
                  <div>
                    <div className="mb-1.5 flex items-center justify-between">
                      <label className="block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                        Extracted text
                      </label>
                      {readDoneType && (
                        <Badge
                          value={readDoneType.label}
                          tone={readDoneType.tone}
                        />
                      )}
                    </div>
                    {readText.trim() ? (
                      <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-xl border border-white/[0.06] bg-ink-900/80 px-3.5 py-3 font-mono text-xs leading-relaxed text-zinc-300">
                        {readText}
                      </pre>
                    ) : (
                      <div className="rounded-xl border border-white/[0.06] bg-ink-900/80 px-3.5 py-3 text-sm text-zinc-500">
                        The file was read but contained no extractable text.
                      </div>
                    )}
                  </div>
                  {readText.trim() && readName && (
                    <SaveToMemoryRow
                      key={`read-${readName}`}
                      filename={readName}
                      text={readText}
                    />
                  )}
                </div>
              )}

              {readText === null && !readError && (
                <Empty icon={<FileDown size={22} />}>
                  Drop a file above, or enter a path and extract its text.
                </Empty>
              )}
            </form>
          </Card>

          {/* ---- Create document ------------------------------------------- */}
          <Card title="Create a document" icon={<FileUp size={15} />}>
            <form onSubmit={create} className="space-y-3.5">
              <div>
                <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                  File name
                </label>
                <div className="flex items-center gap-2">
                  <div className="relative flex-1">
                    <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-accent-soft/70">
                      <writeType.Icon size={15} />
                    </span>
                    <input
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      placeholder="summary.docx"
                      className="field pl-9 font-mono"
                    />
                  </div>
                  <Badge value={writeType.label} tone={writeType.tone} />
                </div>
                <div className="mt-1.5 text-[11px] text-zinc-600">
                  Saved under the daemon&apos;s documents folder — the extension
                  picks the format.
                </div>
              </div>

              <div>
                <div className="mb-1.5 flex items-center justify-between">
                  <label className="block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Contents
                  </label>
                  <VoiceInput
                    size="sm"
                    onTranscript={(chunk) =>
                      setContent((p) => appendDictation(p, chunk))
                    }
                  />
                </div>
                <textarea
                  value={content}
                  onChange={(e) => setContent(e.target.value)}
                  rows={8}
                  placeholder="Write or dictate the document body. Each line becomes a paragraph / row / slide bullet depending on the format…"
                  className="field resize-y"
                />
              </div>

              <div className="flex items-stretch gap-2">
                <button
                  type="submit"
                  disabled={writeBusy || !name.trim() || !content.trim()}
                  className="btn-accent flex-1"
                >
                  {writeBusy ? (
                    <LoaderInline label="Creating…" />
                  ) : (
                    <>
                      <FileUp size={14} /> Create document
                    </>
                  )}
                </button>
                <button
                  type="button"
                  onClick={() => void enhance()}
                  disabled={
                    enhanceBusy || writeBusy || !name.trim() || !content.trim()
                  }
                  title="Have the AI polish the filename and contents — you review before anything is applied"
                  className="btn-ghost shrink-0"
                >
                  {enhanceBusy ? (
                    <LoaderInline label="Polishing… (5-20s)" />
                  ) : (
                    <>
                      <Sparkles size={14} /> Enhance with AI
                    </>
                  )}
                </button>
              </div>

              {enhanceError && <ErrorNote>{enhanceError}</ErrorNote>}

              {suggestion && (
                <div className="space-y-3 rounded-xl border border-accent/25 bg-accent/[0.05] p-3.5">
                  <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.1em] text-accent-soft">
                    <Sparkles size={13} /> AI suggestion — review before
                    applying
                  </div>
                  <div>
                    <div className="mb-1 text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                      Suggested file name
                    </div>
                    <div className="font-mono text-xs text-zinc-200">
                      {suggestion.filename}
                    </div>
                  </div>
                  <div>
                    <div className="mb-1 text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                      Suggested contents
                    </div>
                    <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-lg border border-white/[0.06] bg-ink-900/80 px-3 py-2.5 font-mono text-xs leading-relaxed text-zinc-300">
                      {suggestion.content}
                    </pre>
                  </div>
                  {suggestion.notes && (
                    <div className="text-xs leading-relaxed text-zinc-400">
                      {suggestion.notes}
                    </div>
                  )}
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={applySuggestion}
                      className="btn-accent"
                    >
                      <Sparkles size={14} /> Apply suggestion
                    </button>
                    <button
                      type="button"
                      onClick={() => setSuggestion(null)}
                      className="btn-ghost"
                    >
                      Keep mine
                    </button>
                  </div>
                  <div className="text-[11px] text-zinc-600">
                    Applying only fills the form — nothing is saved until you
                    click Create document.
                  </div>
                </div>
              )}

              {writeOk && (
                <div className="space-y-3">
                  <SuccessNote>
                    Saved{" "}
                    <span className="font-mono text-emerald-100">
                      {writeOk.path}
                    </span>{" "}
                    ({writeOk.bytes.toLocaleString()} bytes).
                  </SuccessNote>
                  <SaveToMemoryRow
                    key={`write-${writeOk.path}`}
                    filename={baseName(writeOk.path)}
                    text={content}
                  />
                </div>
              )}
              {writeError && <ErrorNote>{writeError}</ErrorNote>}

              <div className="text-[11px] text-zinc-600">
                Supported: {SUPPORTED_CREATE}.
              </div>
            </form>
          </Card>
        </div>
      </Reveal>

      <FilePickerModal
        open={browseOpen}
        onClose={() => setBrowseOpen(false)}
        onPick={(path) => setReadPath(path)}
        title="Pick a file to read"
      />
    </PageShell>
  );
}

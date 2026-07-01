"use client";

import { useState } from "react";
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

export default function DocumentsPage() {
  /* ---- Read / extract --------------------------------------------------- */
  const [readPath, setReadPath] = useState("");
  const [readText, setReadText] = useState<string | null>(null);
  const [readDoneType, setReadDoneType] = useState<DocType | null>(null);
  const [readBusy, setReadBusy] = useState(false);
  const [readError, setReadError] = useState<string | null>(null);
  const [readOffline, setReadOffline] = useState(false);

  /* ---- Create document -------------------------------------------------- */
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const [writeBusy, setWriteBusy] = useState(false);
  const [writeError, setWriteError] = useState<string | null>(null);
  const [writeOk, setWriteOk] = useState<DocumentWriteResult | null>(null);
  const [writeOffline, setWriteOffline] = useState(false);

  const writeType = docTypeFor(name);
  const ReadIcon = (readDoneType ?? docTypeFor(readPath)).Icon;

  async function extract(e: React.FormEvent) {
    e.preventDefault();
    if (!readPath.trim()) return;
    setReadBusy(true);
    setReadError(null);
    setReadOffline(false);
    try {
      const data = await get<DocumentRead>(
        `/documents/read?path=${encodeURIComponent(readPath.trim())}`,
      );
      setReadText(data.text ?? "");
      setReadDoneType(docTypeFor(data.path || readPath));
    } catch (err) {
      if (err instanceof ApiError && err.status === 0) setReadOffline(true);
      else setReadError(err instanceof ApiError ? err.message : String(err));
      setReadText(null);
      setReadDoneType(null);
    } finally {
      setReadBusy(false);
    }
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
                    type="submit"
                    disabled={readBusy || !readPath.trim()}
                    className="btn-accent shrink-0"
                  >
                    {readBusy ? (
                      <LoaderInline label="Reading…" />
                    ) : (
                      <>
                        <FolderOpen size={14} /> Extract text
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
                <div>
                  <div className="mb-1.5 flex items-center justify-between">
                    <label className="block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                      Extracted text
                    </label>
                    {readDoneType && (
                      <Badge value={readDoneType.label} tone={readDoneType.tone} />
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
              )}

              {readText === null && !readError && (
                <Empty icon={<FileDown size={22} />}>
                  Enter a file path and extract its text.
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

              <button
                type="submit"
                disabled={writeBusy || !name.trim() || !content.trim()}
                className="btn-accent w-full"
              >
                {writeBusy ? (
                  <LoaderInline label="Creating…" />
                ) : (
                  <>
                    <FileUp size={14} /> Create document
                  </>
                )}
              </button>

              {writeOk && (
                <SuccessNote>
                  Saved{" "}
                  <span className="font-mono text-emerald-100">
                    {writeOk.path}
                  </span>{" "}
                  ({writeOk.bytes.toLocaleString()} bytes).
                </SuccessNote>
              )}
              {writeError && <ErrorNote>{writeError}</ErrorNote>}

              <div className="text-[11px] text-zinc-600">
                Supported: {SUPPORTED_CREATE}.
              </div>
            </form>
          </Card>
        </div>
      </Reveal>
    </PageShell>
  );
}

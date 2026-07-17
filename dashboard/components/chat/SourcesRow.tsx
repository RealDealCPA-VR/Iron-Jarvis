"use client";

// The "Sources" row under an assistant reply that used web tools: one compact,
// favicon-less domain chip per unique URL that ACTUALLY appeared in that turn's
// web_search / web_fetch tool results, each linking out in a new tab. Honest by
// construction — extraction reads the tool cards' own result payloads, never
// links the model merely wrote into its prose.

import { Globe } from "lucide-react";
import type { ToolCard } from "@/lib/useChatStream";

/** The registry's web tools — the pair the composer's Web chip arms, and the
 *  only tools whose results count as sources. */
export const WEB_TOOLS: readonly string[] = ["web_search", "web_fetch"];

/** One source a reply's web tool calls surfaced (persisted on the message). */
export interface ChatSource {
  url: string;
  title?: string;
}

/**
 * Pull the URLs a turn's web tool calls returned out of its finished tool
 * cards. Three honest inputs, deduped in order:
 *   - structured result `data` when the daemon attaches it to the frame
 *     (web_search: {results:[{url,title}]}; web_fetch: {url,title});
 *   - web_fetch's own `url` argument (the page it actually fetched);
 *   - web_search's printed output, one "N. Title\n   URL\n   snippet" block per
 *     hit — bare-URL lines are results. The wire output is clipped at 2000
 *     chars, so a final (possibly half-truncated) line is never trusted.
 */
export function extractWebSources(cards: readonly ToolCard[]): ChatSource[] {
  const seen = new Set<string>();
  const out: ChatSource[] = [];
  const add = (url: unknown, title?: unknown) => {
    if (typeof url !== "string") return;
    const u = url.trim();
    if (!/^https?:\/\//i.test(u) || seen.has(u)) return;
    seen.add(u);
    const t = typeof title === "string" ? title.trim() : "";
    out.push({ url: u, ...(t ? { title: t } : {}) });
  };
  for (const c of cards) {
    if (!WEB_TOOLS.includes(c.name) || c.status !== "done" || c.ok === false) {
      continue;
    }
    const data = (c as { data?: unknown }).data;
    if (data && typeof data === "object") {
      const d = data as { results?: unknown; url?: unknown; title?: unknown };
      if (Array.isArray(d.results)) {
        for (const r of d.results) {
          if (r && typeof r === "object") {
            const rr = r as { url?: unknown; title?: unknown };
            add(rr.url, rr.title);
          }
        }
      }
      add(d.url, d.title);
    }
    if (c.name === "web_fetch") add(c.args?.url);
    if (c.name === "web_search" && c.output) {
      const lines = c.output.split("\n");
      const clipped = c.output.length >= 2000;
      lines.forEach((line, i) => {
        if (clipped && i === lines.length - 1) return; // possibly cut mid-URL
        const t = line.trim();
        if (!/^https?:\/\/\S+$/i.test(t)) return;
        // The result block puts "N. Title" on the line above the URL.
        const m = i > 0 ? lines[i - 1].trim().match(/^\d+\.\s+(.+)$/) : null;
        add(t, m?.[1]);
      });
    }
  }
  return out;
}

/** Hostname without a www. prefix — the chip label. Falls back to the raw URL
 *  when it doesn't parse (never fabricate a prettier name). */
function domainOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "") || url;
  } catch {
    return url;
  }
}

export function SourcesRow({ sources }: { sources: ChatSource[] }) {
  if (sources.length === 0) return null;
  return (
    <div className="ml-11 mt-1 flex min-w-0 flex-wrap items-center gap-1.5">
      <span className="inline-flex shrink-0 items-center gap-1 text-[11px] text-zinc-500">
        <Globe size={10} className="shrink-0 text-accent-soft/70" /> Sources:
      </span>
      {sources.map((s) => (
        <a
          key={s.url}
          href={s.url}
          target="_blank"
          rel="noopener noreferrer"
          title={s.title ? `${s.title} — ${s.url}` : s.url}
          className="max-w-[16rem] truncate rounded-full border border-white/[0.08] bg-white/[0.02] px-2 py-0.5 text-[11px] text-zinc-400 transition-colors hover:border-accent/40 hover:text-accent-soft"
        >
          {domainOf(s.url)}
        </a>
      ))}
    </div>
  );
}

"use client";

/**
 * Memory › Long-term › an MCP memory base's row: "What each tool does"
 * (v1.408.0). The server's own tools are matched to the note work Jarvis
 * does (find, read, save, add to, archive, delete, rename, move), and each
 * argument is told where its value comes from. Jarvis writes to the server
 * ONLY through what the user confirmed here (`saved`); until then it searches
 * and reads.
 *
 * The row's badge comes from the Long-term listing (`bases[].tools_set_up`,
 * passed in as `setUp`). GET /ltm/sources/{name}/roles is a LIVE tools/list
 * of the server (up to 20 s on one that does not answer), so it is read only
 * when the user presses "Set up tools" — and again on every press, so a tool
 * the server gained since is offered (v1.408.0 review). One PUT on "Use
 * these". A 404 (not an MCP base, or an older Iron Jarvis) draws nothing. The
 * words and the rules live in lib/mcpBaseRoles (pure; no lib/api import).
 */

import { useId, useState } from "react";
import { ApiError, get, put } from "@/lib/api";
import { Badge, ErrorNote, Field, LoaderInline, SuccessNote } from "@/components/ui";
import {
  CONST_WORDS,
  EMPTY_WORDS,
  NOT_SET_UP_WORDS,
  NOT_USED_WORDS,
  ROLES_INTRO,
  ROLE_WORDS,
  SAVED_NOTHING_WORDS,
  SAVED_WORDS,
  SET_UP_WORDS,
  SOURCE_WORDS,
  argSelectValue,
  chooseTool,
  decodeRolesView,
  missingRequired,
  rolesBody,
  setArg,
  shownProblems,
  startingChoice,
  toolNamed,
  type McpRolesView,
  type RoleId,
  type RoleMapping,
} from "@/lib/mcpBaseRoles";

const msg = (e: unknown, fallback: string) => (e as { message?: string })?.message || fallback;

export function McpBaseRoles({ name, setUp }: { name: string; setUp?: boolean }) {
  const path = `/ltm/sources/${encodeURIComponent(name)}/roles`;
  const idBase = useId();
  const [view, setView] = useState<McpRolesView | null>(null);
  const [gone, setGone] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [open, setOpen] = useState(false);
  const [choice, setChoice] = useState<Record<string, RoleMapping | null>>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [note, setNote] = useState("");

  if (gone) return null;

  /** Read the server's tools NOW and open the editor on them. */
  async function openEditor() {
    if (loading) return;
    setNote("");
    setSaveError("");
    setLoadError("");
    setLoading(true);
    try {
      const v = decodeRolesView(await get<unknown>(path));
      if (!v) {
        setGone(true);
        return;
      }
      setView(v);
      setChoice(startingChoice(v));
      setOpen(true);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setGone(true);
      else setLoadError(msg(e, "The server's tools couldn't load."));
    } finally {
      setLoading(false);
    }
  }

  async function save() {
    if (!view) return;
    const stop = missingRequired(view, choice);
    if (stop) {
      setSaveError(stop);
      return;
    }
    setSaving(true);
    setSaveError("");
    try {
      const next = decodeRolesView(await put<unknown>(path, rolesBody(view, choice)));
      if (next) setView(next);
      setOpen(false);
      setNote(next && !next.saved ? SAVED_NOTHING_WORDS : SAVED_WORDS);
    } catch (e) {
      setSaveError(msg(e, "Couldn't save the tools."));
    } finally {
      setSaving(false);
    }
  }

  const setRole = (role: RoleId, m: RoleMapping | null) => setChoice((c) => ({ ...c, [role]: m }));
  // The badge: what this editor last read or saved, else the listing's word.
  const isSetUp = view ? !!view.saved : setUp;

  return (
    <div className="mt-2" data-testid={`mcp-roles-${name}`}>
      <div className="flex flex-wrap items-center gap-2">
        {typeof isSetUp === "boolean" && (
          <Badge
            variant="calm"
            keepCase
            value={isSetUp ? SET_UP_WORDS : NOT_SET_UP_WORDS}
            tone={isSetUp ? "green" : "slate"}
            data-testid={`mcp-roles-badge-${name}`}
          />
        )}
        {!open && (
          <button
            type="button"
            data-testid={`mcp-roles-open-${name}`}
            onClick={() => void openEditor()}
            disabled={loading}
            className="btn-ghost btn-sm"
          >
            {loading ? <LoaderInline label="Reading its tools…" /> : "Set up tools"}
          </button>
        )}
      </div>
      {note && <SuccessNote>{note}</SuccessNote>}

      {!open && loadError && (
        <ErrorNote>
          {loadError}{" "}
          <button type="button" onClick={() => void openEditor()} className="underline underline-offset-2">
            Try again
          </button>
        </ErrorNote>
      )}

      {open && view && (
        <form
          data-testid={`mcp-roles-form-${name}`}
          className="mt-3 space-y-4 border-t border-white/[0.06] pt-3"
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <p className="text-[13px] leading-relaxed text-zinc-300">What each tool does</p>
          <p className="text-[12px] leading-relaxed text-zinc-500" data-testid="mcp-roles-intro">
            {ROLES_INTRO}
          </p>
          {view.error && <ErrorNote>{view.error}</ErrorNote>}
          {view.roles.map((role) => {
            const m = choice[role] ?? null;
            const tool = m ? toolNamed(view, m.tool) : undefined;
            const problems = shownProblems(view, choice, role);
            return (
              <div key={role} data-testid={`mcp-role-${role}`} className="space-y-2">
                <Field label={ROLE_WORDS[role]}>
                  <select
                    data-testid={`mcp-role-tool-${role}`}
                    value={m?.tool ?? ""}
                    onChange={(e) => setRole(role, chooseTool(m, toolNamed(view, e.target.value), e.target.value))}
                    className="field w-full max-w-sm"
                  >
                    <option value="">{NOT_USED_WORDS}</option>
                    {/* A confirmed tool the listing lacks: gone from the server —
                        or the server did not answer, and then nothing is known. */}
                    {m && !tool && (
                      <option value={m.tool}>
                        {view.error ? m.tool : `${m.tool} (the server no longer offers it)`}
                      </option>
                    )}
                    {view.tools.map((t) => (
                      <option key={t.name} value={t.name} title={t.description || undefined}>
                        {t.name}
                      </option>
                    ))}
                  </select>
                </Field>
                {m && tool && tool.params.length > 0 && (
                  <ul className="space-y-2 pl-3" data-testid={`mcp-role-args-${role}`}>
                    {tool.params.map((p) => {
                      const current = m.args[p.name];
                      const value = argSelectValue(current);
                      const argId = `${idBase}-${role}-${p.name}`;
                      return (
                        <li key={p.name} className="flex flex-wrap items-center gap-2">
                          <label htmlFor={argId} className="min-w-[8rem] text-[12px] text-zinc-400">
                            <span className="font-mono">{p.name}</span>
                            {p.required && <span className="text-zinc-500"> · required</span>}
                          </label>
                          <select
                            id={argId}
                            data-testid={`mcp-arg-${role}-${p.name}`}
                            value={value}
                            onChange={(e) => {
                              const keep = current && typeof current !== "string" ? current.const : "";
                              setRole(role, setArg(m, p.name, e.target.value, keep));
                            }}
                            className="field w-auto min-w-0 max-w-[16rem] py-1 text-sm"
                          >
                            {p.required ? (
                              value === "" && (
                                <option value="" disabled hidden>
                                  Choose where it comes from
                                </option>
                              )
                            ) : (
                              <option value="">{EMPTY_WORDS}</option>
                            )}
                            {view.sources.map((s) => (
                              <option key={s} value={s}>
                                {SOURCE_WORDS[s]}
                              </option>
                            ))}
                            <option value="const">{CONST_WORDS}</option>
                          </select>
                          {current && typeof current !== "string" && (
                            <input
                              data-testid={`mcp-arg-const-${role}-${p.name}`}
                              aria-label={`Fixed value for ${p.name}`}
                              value={current.const}
                              onChange={(e) => setRole(role, setArg(m, p.name, "const", e.target.value))}
                              className="field w-auto min-w-0 max-w-[12rem] py-1 text-sm"
                            />
                          )}
                        </li>
                      );
                    })}
                  </ul>
                )}
                {problems.length > 0 && (
                  <ul className="space-y-0.5 text-[12px] text-tone-warn" data-testid={`mcp-role-problems-${role}`}>
                    {problems.map((w) => (
                      <li key={w}>{w}</li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
          {saveError && <ErrorNote>{saveError}</ErrorNote>}
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="submit"
              data-testid={`mcp-roles-save-${name}`}
              disabled={saving}
              className="btn-accent btn-sm"
            >
              {saving ? <LoaderInline label="Saving…" /> : "Use these"}
            </button>
            <button type="button" onClick={() => setOpen(false)} className="btn-ghost btn-sm">
              Cancel
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

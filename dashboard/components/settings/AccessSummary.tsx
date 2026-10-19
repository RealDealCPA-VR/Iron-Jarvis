"use client";

/**
 * WHAT JARVIS CAN REACH (v1.352.0, G27) — the calm block at the top of
 * Settings › Permissions & ledger.
 *
 * Nowhere used to say that chat and agents read any file on this PC (except
 * the folders holding the app's keys and records) WITHOUT ASKING, at every
 * approval level, "Ask first" included. This block says it, and the rest of
 * what Jarvis can reach, in one place. It is READ-ONLY and changes nothing:
 * every fact comes from data the app already holds, and each "Change" press
 * opens the place that setting really lives.
 *
 *   Reading files    GET /settings/access (core/fs_policy: the protected
 *                    folders, the "Folders Jarvis may read" setting (v1.356.0,
 *                    G81 — `chosen`, with a press to the row that changes
 *                    it), and an install's own limit when it sets one);
 *                    READS_LINE is lib/permissionLevels' sentence
 *   Saving files     the file tools' confinement (the chat's or project's
 *                    folder); the chat folder from the same route
 *   Connected apps   GET /connectors (green only when it works: the
 *                    Directory's own `connected && status === "connected"`)
 *   Your browser /   the saved settings values SettingsHome already read,
 *   Agent browser    worded by the ONE schema's labels and options
 *   Approval level   the level new chats start in on this window, read the
 *                    way the chat page reads it (lib/permissionLevels)
 *   Standing grants  GET /grants (live ones)
 *
 * A fact that cannot be read says so; it never reads as "nothing".
 */

import { useId, type ReactNode } from "react";
import { useApi } from "@/lib/useApi";
import {
  PERMISSION_LEVELS,
  READS_LINE,
  permissionToneText,
  readChatPermissionMode,
  type PermissionMode,
} from "@/lib/permissionLevels";
import type { SchemaSetting } from "@/components/settings/SettingRow";

/** GET /settings/access (daemon/routes/access_summary.py). `chosen` (v1.356.0,
 *  G81) = the folders the user listed in "Folders Jarvis may read", as typed;
 *  `install` = the installation's own limit is on. An older daemon sends
 *  neither. */
export interface AccessFacts {
  reads?: { limited?: boolean; folders?: string[]; chosen?: string[]; install?: boolean };
  writes?: { chat_files_dir?: string };
}

/** The settings row that changes what Jarvis may read (SettingRow's id). */
export const READ_FOLDERS_ROW = "setting-readable_folders";

/** The fields of a GET /connectors row this block reads. */
interface ConnectorRow {
  id: string;
  name?: string;
  category?: string;
  connected?: boolean;
  status?: string;
}

/** The protected folders, in words (fs_policy's protected roots: the key
 *  folders, the undo journal and the database). */
export const NEVER_READ_LINE = "The folders that hold its keys and its own records are never read.";

/** "Reading files": what fs_policy enforces, in words. `facts` null = the
 *  route could not be read (an older daemon): the desktop default, said with
 *  its one exception. */
export function readsSentence(facts: AccessFacts | null): string {
  const reads = facts?.reads;
  if (!facts || !reads) {
    return `${READS_LINE}, at every approval level, unless this install limits it to chosen folders. ${NEVER_READ_LINE}`;
  }
  if (reads.limited) {
    // v1.356.0 (G81): the user's own folders, named as they typed them.
    const chosen = (reads.chosen ?? []).filter((f) => typeof f === "string" && f.trim());
    if (chosen.length > 0) {
      const where = reads.install ? " (where this install allows)" : "";
      return `Reads files only in the folders you chose${where}, without asking: ${chosen.join(", ")}, and in its own working folders. ${NEVER_READ_LINE}`;
    }
    const folders = (reads.folders ?? []).filter((f) => typeof f === "string" && f.trim());
    if (folders.length === 0) {
      return "This install limits reading to a list of folders, and none of them could be found, so it reads no files.";
    }
    return `Reads files only in the folders this install allows, without asking: ${folders.join(", ")}. ${NEVER_READ_LINE}`;
  }
  return `${READS_LINE}, at every approval level. ${NEVER_READ_LINE}`;
}

/** "Saving files": the file tools' confinement, and where a chat outside a
 *  project keeps its folder when the daemon says. */
export function writesSentence(facts: AccessFacts | null): string {
  const dir = (facts?.writes?.chat_files_dir ?? "").trim();
  const where = dir ? ` A chat outside a project keeps its folder in ${dir}.` : "";
  return (
    "Saves and changes files only in the folder it was given to work in: the chat's own folder or the project's folder." +
    where +
    " A command it runs is not held to those folders, which is why it asks before running one unless the level is Don't ask."
  );
}

/** The connected apps, by name (the Memory sources are not apps). A saved
 *  connection that does not work is named as such; one the user switched off
 *  (v1.356.0, G78) says "off", never "not working". */
export function connectedApps(rows: ConnectorRow[] | null | undefined): string[] {
  return (rows ?? [])
    .filter((r) => r && r.connected === true && r.category !== "Memory")
    .map((r) => {
      const name = (r.name || r.id || "").trim();
      if (r.status === "off") return `${name} (off)`;
      return r.status === "connected" ? name : `${name} (not working)`;
    })
    .filter(Boolean);
}

function optionLabel(def: SchemaSetting | undefined, value: unknown): string | null {
  const hit = def?.options?.find((o) => o.value === value);
  return hit ? hit.label : null;
}

function Row({ label, children, testId }: { label: string; children: ReactNode; testId: string }) {
  return (
    <div data-testid={testId} className="grid grid-cols-[minmax(0,1fr)] gap-1 px-3 py-3 sm:grid-cols-[10rem_minmax(0,1fr)] sm:gap-6">
      <dt className="text-[13px] font-medium text-zinc-100">{label}</dt>
      <dd className="min-w-0 text-[12px] leading-relaxed text-zinc-400">{children}</dd>
    </div>
  );
}

function Go({ onPress, children }: { onPress?: () => void; children: ReactNode }) {
  if (!onPress) return null;
  return (
    <button
      type="button"
      onClick={onPress}
      className="ml-1 rounded text-zinc-300 underline decoration-white/20 underline-offset-2 hover:text-zinc-100 hover:decoration-current focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
    >
      {children}
    </button>
  );
}

export function AccessSummary({
  values,
  defs,
  onGo,
}: {
  /** The SAVED settings values (what is in force), as SettingsHome read them. */
  values: Record<string, unknown>;
  /** The schema rows, for the settings' own labels and option words. */
  defs: SchemaSetting[];
  /** Open a settings group (and one of its sections). */
  onGo?: (group: string, sub?: string) => void;
}) {
  const headingId = useId();
  // v1.356.0 review (G81): re-read what the gate enforces when the SAVED
  // folders change — a Save on the row below must not leave this block
  // saying "reads anywhere" above a limit that is already in force.
  const readKey = JSON.stringify(values.readable_folders ?? null);
  const access = useApi<AccessFacts>("/settings/access", [readKey]);
  const connectors = useApi<{ connectors?: ConnectorRow[] }>("/connectors");
  const grants = useApi<{ grants?: unknown[] }>("/grants");

  const def = (key: string) => defs.find((d) => d.key === key);

  // Reading / saving: the route's facts; any failure reads as "unknown".
  const facts = access.data ?? (access.error ? null : undefined);

  // Apps.
  const apps = connectedApps(connectors.data?.connectors);
  const autoApps = values.mcp_auto_approve === true;
  const autoLabel = def("mcp_auto_approve")?.label ?? "Run app tools without asking";

  // Browser and agent browser, in the schema's own words.
  const browserDef = def("browser_access");
  const browserValue = values.browser_access;
  const browserWords = optionLabel(browserDef, browserValue);
  const agentDef = def("agent_browser_enabled");
  const agentOn = values.agent_browser_enabled;

  // The level new chats start in, on this window.
  const mode: PermissionMode = readChatPermissionMode();
  const level = PERMISSION_LEVELS[mode];

  // Standing grants.
  const grantCount = grants.data?.grants?.length ?? null;

  return (
    <section
      aria-labelledby={headingId}
      data-testid="access-summary"
      className="rounded-2xl border border-white/[0.06] bg-white/[0.015]"
    >
      <h3 id={headingId} className="px-3 pt-3 text-[12px] font-semibold uppercase tracking-wide text-zinc-500">
        What Jarvis can reach
      </h3>
      <dl className="divide-y divide-white/[0.04]">
        <Row label="Reading files" testId="access-reads">
          {facts === undefined ? "Checking…" : readsSentence(facts)}
          {/* v1.356.0 (G81): the row that changes it, right below. */}
          {facts && def("readable_folders") && (
            <Go onPress={onGo ? () => onGo("permissions", READ_FOLDERS_ROW) : undefined}>
              {(facts.reads?.chosen ?? []).some((f) => typeof f === "string" && f.trim())
                ? "Change folders"
                : "Choose folders"}
            </Go>
          )}
        </Row>
        <Row label="Saving files" testId="access-writes">
          {facts === undefined ? "Checking…" : writesSentence(facts)}
        </Row>
        <Row label="Connected apps" testId="access-apps">
          {connectors.data ? (
            <>
              {apps.length > 0 ? `${apps.join(", ")}.` : "None yet."}
              {apps.length > 0 && autoApps && ` Their tools run without asking (${autoLabel} is on).`}
            </>
          ) : connectors.error ? (
            "Couldn’t read the list of apps."
          ) : (
            "Checking…"
          )}
          <Go onPress={onGo ? () => onGo("connections", "connections-apps") : undefined}>Manage apps</Go>
        </Row>
        {browserWords && (
          <Row label={browserDef?.label ?? "Your browser"} testId="access-browser">
            {browserValue === "off"
              ? `${browserWords}. Jarvis can't see or use your own browser.`
              : browserValue === "read_only"
                ? `${browserWords}: it can read the pages open in your own browser.`
                : `${browserWords}: it can read and act in your own browser.`}
            <Go onPress={onGo ? () => onGo("connections", "connections-browser") : undefined}>Change</Go>
          </Row>
        )}
        {typeof agentOn === "boolean" && (
          <Row label={agentDef?.label ?? "Agent browser"} testId="access-agent-browser">
            {agentOn
              ? `On. ${agentDef?.help ?? "Agents can drive their own separate browser."}`
              : "Off. Agents can't drive a browser of their own."}
          </Row>
        )}
        <Row label="Approval level" testId="access-level">
          <span className={`font-medium ${permissionToneText(level.tone) || "text-zinc-200"}`} data-mode={mode}>
            {level.label}
          </span>
          {". "}New chats in this window start here; each chat can change it from the composer. {level.description}.
        </Row>
        <Row label="Standing grants" testId="access-grants">
          {grantCount != null ? (
            grantCount === 0 ? (
              "None. Every ask follows the approval level."
            ) : (
              `${grantCount} approval${grantCount === 1 ? "" : "s"} you gave stay${grantCount === 1 ? "s" : ""} in place and skip${grantCount === 1 ? "s" : ""} the ask for exactly that action.`
            )
          ) : grants.error ? (
            grants.error.status === 503 ? "Not available on this version." : "Couldn’t read them."
          ) : (
            "Checking…"
          )}
          {grantCount != null && grantCount > 0 && (
            <Go onPress={onGo ? () => onGo("permissions", "permissions-grants") : undefined}>Review</Go>
          )}
        </Row>
      </dl>
    </section>
  );
}

export default AccessSummary;

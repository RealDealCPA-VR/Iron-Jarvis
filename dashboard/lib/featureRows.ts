/**
 * Three ways of working that live INSIDE a composer, not on a page of their
 * own (v1.455.0, readiness Wave G, item 25): the night shift (Chat › Code's
 * Tonight list), Try on several models (a coding session's Try on chip) and
 * Local only (the chat's chip). Nobody found them outside the composer. Each
 * is now a row in Help, on Everything and in Ctrl K, leading to the
 * Handbook's own section on it (the Help page's guide viewer — a link that
 * resolves today, pinned by __tests__/feature-rows-v1455.test.ts against the
 * Handbook's headings through lib/helpLinks' section rule).
 *
 * Import-free apart from lib/helpLinks (the Help page, lib/surfaces and the
 * tests read it).
 */

import { helpGuideHref } from "@/lib/helpLinks";

export interface FeatureRow {
  /** Stable id (the icon lib/surfaces gives it). */
  id: "night-shift" | "try-on" | "local-only";
  href: string;
  title: string;
  desc: string;
  aliases: string[];
  /** The Handbook heading the row opens (its section slug's source). */
  section: string;
}

export const FEATURE_ROWS: readonly FeatureRow[] = [
  {
    id: "night-shift",
    title: "Night shift",
    section: "Night shift: the Tonight list",
    href: helpGuideHref("handbook", "Night shift: the Tonight list"),
    desc: "Queue coding tasks for tonight in Chat › Code's Tonight list; each runs on its own branch and you get one summary in the morning.",
    aliases: ["tonight", "overnight", "night tasks", "run while I sleep", "queue for tonight", "night"],
  },
  {
    id: "try-on",
    title: "Try on several models",
    section: "Try one task on several models",
    href: helpGuideHref("handbook", "Try one task on several models"),
    desc: "In a coding session, the Try on chip runs one task on up to three models, each on its own branch, and you keep the one you like.",
    aliases: ["try on", "compare models", "several models", "which model is best", "attempts"],
  },
  {
    id: "local-only",
    title: "Local only",
    section: "Local-only chats, and a faster app",
    href: helpGuideHref("handbook", "Local-only chats, and a faster app"),
    desc: "The Local only chip keeps a chat on this PC or your own network: nothing goes to a cloud model without asking you first.",
    aliases: ["local only", "keep it local", "private chat", "no cloud", "stay on this pc", "client data"],
  },
];

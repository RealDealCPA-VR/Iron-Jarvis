/**
 * THE SIMPLE MENU (v1.318.0): seven places a person understands, by the job
 * they came to do — not 33 pages grouped the way the software is built.
 *
 *   Home · Work · Files · Automations · About me · Apps & settings · Help
 *
 * Each place ("hub") is a set of existing pages. Nothing moved and nothing
 * was removed: every page keeps its address, Ctrl K still finds all of them,
 * and the Advanced switch still shows today's full menu. In Simple mode the
 * side menu lists the hubs, and a page that belongs to one shows the hub's
 * pages as tabs above its title (components/HubTabs.tsx), so the next page
 * is one press away without opening the menu.
 *
 * A tab's words are the page's own name (the title bar's crumb, lib/nav.ts)
 * — a tab that says something different from the title it opens would be a
 * second name to learn. `simple: false` tabs are left out of the tab row in Simple mode
 * unless you are ON that page (then it shows, so you can see where you are).
 * Every page in lib/nav.ts belongs to exactly one hub — pinned by test, so a
 * new page cannot ship unreachable from the Simple menu's tabs.
 */

import {
  FolderOpen,
  House,
  LifeBuoy,
  MessageSquare,
  Settings2,
  UserRound,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { NAV_ENTRIES, NON_RAIL_ENTRIES, labelForPath } from "./nav";

export interface HubTab {
  href: string;
  /** Shown in the Simple tab row; false = only while you are on it. */
  simple: boolean;
}

export interface Hub {
  key: string;
  label: string;
  icon: LucideIcon;
  /** One plain line: what this place is for (Home tiles, menu tooltips). */
  blurb: string;
  /** Where the menu entry goes. */
  href: string;
  tabs: HubTab[];
}

const t = (href: string, simple = true): HubTab => ({ href, simple });

export const HUBS: Hub[] = [
  {
    key: "home",
    label: "Home",
    icon: House,
    blurb: "Start here: ask for something, or pick up where you left off.",
    href: "/",
    tabs: [t("/")],
  },
  {
    key: "work",
    label: "Work",
    icon: MessageSquare,
    blurb: "Chat with Jarvis, keep work in projects, and hand bigger jobs to the team.",
    href: "/chat",
    tabs: [
      t("/chat"),
      t("/projects"),
      t("/agents"),
      t("/creative"),
      t("/terminals"),
      t("/templates", false),
      t("/self-dev", false),
    ],
  },
  {
    key: "files",
    label: "Files",
    icon: FolderOpen,
    blurb: "Read, write and tidy documents, and find any file on this PC.",
    href: "/documents",
    tabs: [t("/documents"), t("/filesearch"), t("/artifacts", false)],
  },
  {
    key: "automations",
    label: "Automations",
    icon: Zap,
    blurb: "Work that runs by itself: step-by-step workflows and jobs on a timer.",
    href: "/workflows",
    tabs: [
      t("/workflows"),
      t("/schedules"),
      t("/reflex", false),
      t("/sentinels", false),
      t("/autonomy", false),
      t("/webhooks", false),
    ],
  },
  {
    key: "me",
    label: "About me",
    icon: UserRound,
    blurb: "Who you are, how you like answers, and what Jarvis remembers.",
    href: "/you",
    tabs: [t("/you"), t("/train"), t("/memory"), t("/skills", false)],
  },
  {
    key: "settings",
    label: "Apps & settings",
    icon: Settings2,
    blurb: "Connect your AI and apps, choose where alerts go, and change settings.",
    href: "/connections",
    tabs: [
      t("/connections"),
      t("/marketplace"),
      t("/channels"),
      t("/settings"),
      t("/updates"),
      t("/sessions", false),
      t("/kanban", false),
      t("/activity", false),
      t("/usage", false),
      t("/tools", false),
      t("/computeruse", false),
      t("/secrets", false),
      t("/fleet", false),
    ],
  },
  {
    key: "help",
    label: "Help",
    icon: LifeBuoy,
    blurb: "What Iron Jarvis can do, and a guide that answers questions about it.",
    href: "/help",
    tabs: [t("/help")],
  },
];

/** The tab's words: the name the page goes by in the title bar (its own
 *  title — "Directory", "Fleet"), else its menu label. */
export function tabLabel(href: string): string {
  const own = labelForPath(href);
  if (own) return own;
  const entry = [...NAV_ENTRIES, ...NON_RAIL_ENTRIES].find((e) => e.href === href);
  return entry?.label ?? href.replace(/^\//, "");
}

/** True when `pathname` is `href` or a page under it ("/sessions/abc"). */
export function onPage(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

/** The hub a page belongs to (longest matching tab wins), or null. */
export function hubFor(pathname: string | null | undefined): { hub: Hub; tab: HubTab } | null {
  if (!pathname) return null;
  let best: { hub: Hub; tab: HubTab } | null = null;
  for (const hub of HUBS) {
    for (const tab of hub.tabs) {
      if (onPage(pathname, tab.href) && (!best || tab.href.length > best.tab.href.length)) {
        best = { hub, tab };
      }
    }
  }
  return best;
}

/** The tabs shown on `pathname` in Simple mode (its own tab always included). */
export function visibleTabs(hub: Hub, pathname: string): HubTab[] {
  return hub.tabs.filter((tab) => tab.simple || onPage(pathname, tab.href));
}

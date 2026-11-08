/**
 * The five places the Help page leads with (v1.450.0, readiness item 41).
 *
 * The Help page described a menu that no longer exists (Simple mode's seven
 * places, the Overview's tiles). Today the sidebar has five places
 * (`lib/surfaces.SIDEBAR_HREFS`), and this list is the Help page's words for
 * them, in the sidebar's order. `__tests__/help-today-v1450.test.tsx` holds
 * the two equal, so a sixth sidebar place (or one taken away) turns the test
 * red until Help says so too.
 *
 * Import-free on purpose (the test reads it without the page).
 */

export interface HelpPlace {
  href: string;
  title: string;
  desc: string;
}

export const HELP_PLACES: readonly HelpPlace[] = [
  {
    href: "/chat",
    title: "Chat",
    desc: "Ask anything, attach files, and let it work. The Chat | Code switch at the top opens coding sessions that write, run and check code in a folder you pick.",
  },
  {
    href: "/terminals",
    title: "Build",
    desc: "Your terminals, and the Code view that drives your own Claude Code, Codex, Pi, OpenCode, Grok or Antigravity.",
  },
  {
    href: "/projects",
    title: "Projects",
    desc: "One place per client or piece of work: its brief, its folder and the knowledge every chat inside it starts from.",
  },
  {
    href: "/everything",
    title: "Everything",
    desc: "Every other page, with one line each, and Status: what is running and how the app is doing.",
  },
  {
    href: "/settings",
    title: "Settings",
    desc: "Models, connections, automation, memory, permissions, appearance and system. Search finds any setting by what you would call it.",
  },
];

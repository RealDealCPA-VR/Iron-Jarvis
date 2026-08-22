"use client";

import { useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Boxes,
  Bot,
  Workflow,
  CalendarClock,
  Database,
  FileText,
  FileSearch,
  PlugZap,
  MonitorCog,
  GitBranch,
  ArrowRight,
  CheckCircle2,
  Eye,
  Smartphone,
  MessageCircle,
  MessageSquare,
  FolderKanban,
  SquareTerminal,
  Images,
  GraduationCap,
  LifeBuoy,
  Megaphone,
  Wifi,
  KeyRound,
  ShieldCheck,
  BookOpen,
  UserRound,
  X,
  type LucideIcon,
} from "lucide-react";
import { ApiError, get } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { Card, ErrorNote, LoaderInline, SkeletonRows } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";

interface Subsystem {
  href: string;
  title: string;
  icon: LucideIcon;
  desc: string;
}

const SUBSYSTEMS: Subsystem[] = [
  // The four HERO surfaces lead — the exact pages the default Simple-mode nav
  // leads with (v1.198.0; the grid used to omit all four).
  {
    href: "/chat",
    title: "Chat",
    icon: MessageSquare,
    desc: "One surface for everything — ask, attach files, type / for skills; big jobs escalate themselves.",
  },
  {
    href: "/projects",
    title: "Projects",
    icon: FolderKanban,
    desc: "The context spine: a brief, instructions, a real folder, and knowledge every chat and task inside it inherits.",
  },
  {
    href: "/terminals",
    title: "Build",
    icon: SquareTerminal,
    desc: "Live terminals side by side, each openable in any project folder.",
  },
  {
    href: "/creative",
    title: "Creative",
    icon: Images,
    desc: "Generate images, video, music and speech; browse it all in a library.",
  },
  {
    href: "/you",
    title: "You",
    icon: UserRound,
    desc: "Who you are, how you want answers written, and which language — carried into every model, in chat and in agent runs alike.",
  },
  {
    href: "/sessions",
    title: "Sessions",
    icon: Boxes,
    desc: "Hand an agent a task in plain language and watch it work the job end to end, step by step.",
  },
  {
    href: "/agents",
    title: "Agents",
    icon: Bot,
    desc: "The built-in roles (builder, planner, reviewer, and more) plus any custom agents you define.",
  },
  {
    href: "/workflows",
    title: "Workflows",
    icon: Workflow,
    desc: "Chain several sessions into a repeatable, multi-step pipeline that runs as one unit.",
  },
  {
    href: "/schedules",
    title: "Schedules",
    icon: CalendarClock,
    desc: "Run tasks on a recurring or one-time schedule — pick a friendly preset, no cron syntax needed.",
  },
  {
    href: "/memory?scope=longterm",
    title: "Memory & long-term memory",
    icon: Database,
    desc: "Search and append durable notes across the built-in brain and your own Obsidian or Notion sources.",
  },
  {
    href: "/documents",
    title: "Documents",
    icon: FileText,
    desc: "Read and write files in your workspace so agents can work with real documents.",
  },
  {
    href: "/filesearch",
    title: "File search",
    icon: FileSearch,
    desc: "Find files and matching text across your project folders in a flash.",
  },
  {
    href: "/connections",
    title: "Connections & secrets",
    icon: PlugZap,
    desc: "Connect a model with an API key or OAuth; keys and tokens live in an encrypted, write-only vault.",
  },
  {
    href: "/channels",
    title: "Notifications",
    icon: Megaphone,
    desc: "Where alerts go — and a destination can be two-way, so you chat with Iron Jarvis from your phone.",
  },
  {
    href: "/computeruse",
    title: "Computer use",
    icon: MonitorCog,
    desc: "Opt-in browser and desktop control, fenced by a domain/action allowlist and human approval gates.",
  },
  {
    href: "/self-dev",
    title: "Self-development",
    icon: GitBranch,
    desc: "Let a Maintainer improve Iron Jarvis's own code on a throwaway worktree — always review-gated.",
  },
];

/** Inline monospace snippet, matching the daemon-offline hint styling. */
function Code({ children }: { children: ReactNode }) {
  return (
    <code className="rounded bg-black/40 px-1.5 py-0.5 font-mono text-[11px] text-accent-soft/90">
      {children}
    </code>
  );
}

/** A keycap-style token for names you type or click. */
function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded border border-white/10 bg-white/[0.04] px-1.5 py-0.5 font-mono text-[11px] text-zinc-300">
      {children}
    </kbd>
  );
}

interface GlossaryTerm {
  term: string;
  def: ReactNode;
}

const GLOSSARY: GlossaryTerm[] = [
  {
    term: "Session",
    def: "One task you hand an agent — it plans, uses tools, and returns a result.",
  },
  {
    term: "Agent",
    def: "A specialized worker with its own focus, like Builder, Planner, or Reviewer.",
  },
  {
    term: "Workflow",
    def: "A saved, repeatable series of steps that runs several sessions as one unit.",
  },
  {
    term: "Skill",
    def: "A reusable instruction set an agent can pull in when a task needs it.",
  },
  {
    term: "Long-term memory",
    def: "Durable notes it can search and add to — a local folder, Notion, or a remote SSH folder.",
  },
  {
    term: "Sentinels / Watchers",
    def: "Background watchers that suggest tasks based on what they notice. Off by default.",
  },
  {
    term: "Autonomy",
    def: "Lets Iron Jarvis act on its own within limits you set. Off by default.",
  },
  {
    term: "Computer use",
    def: "Opt-in control of the browser or desktop, gated by your approvals.",
  },
  {
    term: "Connections",
    def: "Your model accounts — Claude, OpenAI, and others — used to run agents.",
  },
  {
    term: "Two-way destination",
    def: "A Notifications destination you can talk back to — messages you send it become real conversations with Iron Jarvis, shared with the desktop Chat page.",
  },
  {
    term: "Terminals",
    def: "Real shells on your machine that agents can run commands in.",
  },
];

interface LoopStep {
  icon: LucideIcon;
  title: string;
  desc: string;
}

// Chat-first (v1.198.0): the product thesis is ONE chat surface that escalates
// itself — the loop used to describe the advanced Sessions lane instead.
const LOOP: LoopStep[] = [
  {
    icon: MessageSquare,
    title: "Ask in Chat",
    desc: "Describe what you want in plain language — quick answers come straight back.",
  },
  {
    icon: Bot,
    title: "It escalates itself",
    desc: "Real multi-step work hands itself to a full agent — visibly, and with a reason.",
  },
  {
    icon: CheckCircle2,
    title: "Review & approve",
    desc: "Risky changes wait for your sign-off before anything lands.",
  },
];

/* ------------------------------------------------------------------ guides */

/** One entry of GET /helpdocs — the catalog of in-app guides. */
interface HelpDocMeta {
  slug: string;
  title: string;
  description: string;
}

/** GET /helpdocs/{slug} — one guide's full markdown. */
interface HelpDocBody {
  slug: string;
  title: string;
  markdown: string;
}

// Dark-theme element overrides for the guide viewer (the app has no typography
// plugin — same hand-rolled "prose-invert" approach as the Chat page, sized a
// notch roomier because a handbook is read top to bottom, not in bubbles).
const GUIDE_MD: Components = {
  h1: ({ children }) => (
    <h1 className="mb-2 mt-5 text-lg font-semibold text-zinc-100 first:mt-0">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="mb-1.5 mt-4 text-base font-semibold text-zinc-100 first:mt-0">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="mb-1 mt-3 text-sm font-semibold text-zinc-100 first:mt-0">{children}</h3>
  ),
  h4: ({ children }) => (
    <h4 className="mb-1 mt-2.5 text-[13px] font-semibold text-zinc-200 first:mt-0">
      {children}
    </h4>
  ),
  p: ({ children }) => (
    <p className="my-1.5 leading-relaxed first:mt-0 last:mb-0">{children}</p>
  ),
  ul: ({ children }) => <ul className="my-1.5 list-disc space-y-1 pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="my-1.5 list-decimal space-y-1 pl-5">{children}</ol>,
  li: ({ children }) => <li className="leading-relaxed [&>p]:my-0">{children}</li>,
  table: ({ children }) => (
    <div className="my-2 overflow-x-auto">
      <table className="w-full border-collapse text-[13px]">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border border-white/10 bg-white/[0.05] px-2.5 py-1.5 text-left font-medium text-zinc-100">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border border-white/10 px-2.5 py-1.5 align-top text-zinc-300">
      {children}
    </td>
  ),
  a: ({ children, href }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-accent-soft underline decoration-accent/40 underline-offset-2 transition-colors hover:decoration-accent"
    >
      {children}
    </a>
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-2 border-l-2 border-accent/40 pl-3 text-zinc-400 [&>p]:my-0.5">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-3 border-white/10" />,
  strong: ({ children }) => (
    <strong className="font-semibold text-zinc-100">{children}</strong>
  ),
  // The pre override resets the inline-code styling for fenced blocks so code
  // inside a block is mono-on-dark, not pill-highlighted per line.
  pre: ({ children }) => (
    <pre className="my-2 overflow-x-auto rounded-xl border border-white/[0.06] bg-black/40 p-3 font-mono text-[11.5px] leading-relaxed text-zinc-300 [&>code]:rounded-none [&>code]:bg-transparent [&>code]:p-0 [&>code]:text-inherit">
      {children}
    </pre>
  ),
  code: ({ children, className }) => (
    <code
      className={`rounded bg-white/[0.08] px-1.5 py-0.5 font-mono text-[0.85em] text-accent-soft ${className ?? ""}`}
    >
      {children}
    </code>
  ),
};

/**
 * The deep docs (Handbook, Recommended Settings, Local Models), served by the
 * daemon and read WITHOUT leaving the app — packaged users have no repo to
 * browse (v1.198.0). Clicking a card expands an in-page viewer below the list;
 * clicking the open card again (or the ✕) closes it. Deliberately local state,
 * not a ?doc= search param: useSearchParams forces a Suspense boundary and
 * risks the static build.
 */
function GuidesCard() {
  const { data, error, loading } = useApi<{ docs: HelpDocMeta[] }>("/helpdocs");
  const [openSlug, setOpenSlug] = useState<string | null>(null);
  const [doc, setDoc] = useState<HelpDocBody | null>(null);
  const [docLoading, setDocLoading] = useState(false);
  const [docError, setDocError] = useState<string | null>(null);
  // Guards a stale response landing after the user switched guides (or closed
  // the viewer): only the fetch for the CURRENTLY open slug may set state.
  const wanted = useRef<string | null>(null);

  const close = () => {
    wanted.current = null;
    setOpenSlug(null);
    setDoc(null);
    setDocError(null);
    setDocLoading(false);
  };

  const openGuide = (slug: string) => {
    if (openSlug === slug) {
      close(); // second click on the open guide toggles it shut
      return;
    }
    wanted.current = slug;
    setOpenSlug(slug);
    setDoc(null);
    setDocError(null);
    setDocLoading(true);
    get<HelpDocBody>(`/helpdocs/${encodeURIComponent(slug)}`)
      .then((d) => {
        if (wanted.current !== slug) return;
        setDoc(d);
        setDocLoading(false);
      })
      .catch((e: unknown) => {
        if (wanted.current !== slug) return;
        // The daemon 404s honestly when a doc is missing from the install —
        // show its message, never a blank panel.
        setDocError(e instanceof ApiError ? e.message : String(e));
        setDocLoading(false);
      });
  };

  const docs = data?.docs ?? [];
  const openMeta = docs.find((d) => d.slug === openSlug);

  return (
    <Card title="Guides" icon={<GraduationCap size={15} />}>
      <p className="text-[13px] leading-relaxed text-zinc-400">
        The deep documentation, readable right here — no repo or website needed.
      </p>

      {loading && (
        <div className="mt-4">
          <SkeletonRows rows={2} />
        </div>
      )}
      {!loading && error && (
        <div className="mt-4">
          <ErrorNote>
            Couldn&apos;t load the guides
            {error.status === 0 ? " — the daemon is offline" : ""}: {error.message}
          </ErrorNote>
        </div>
      )}
      {!loading && !error && docs.length === 0 && (
        <p className="mt-4 text-[13px] text-zinc-500">No guides shipped with this build.</p>
      )}

      {docs.length > 0 && (
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          {docs.map((d) => {
            const isOpen = d.slug === openSlug;
            return (
              <button
                key={d.slug}
                type="button"
                onClick={() => openGuide(d.slug)}
                aria-expanded={isOpen}
                className={`group flex flex-col gap-2 rounded-2xl border px-4 py-4 text-left transition-all duration-300 ${
                  isOpen
                    ? "border-accent/30 bg-accent/[0.06]"
                    : "border-white/[0.05] bg-white/[0.02] hover:-translate-y-0.5 hover:border-white/10"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="grid h-9 w-9 place-items-center rounded-xl border border-white/[0.08] bg-white/[0.03] text-accent-soft">
                    <BookOpen size={16} />
                  </span>
                  <ArrowRight
                    size={14}
                    className={`transition-transform ${
                      isOpen ? "rotate-90 text-accent-soft" : "text-zinc-600 group-hover:text-accent-soft"
                    }`}
                  />
                </div>
                <div>
                  <div className="text-sm font-semibold text-zinc-100">{d.title}</div>
                  <p className="mt-1 text-[12px] leading-relaxed text-zinc-500">
                    {d.description}
                  </p>
                </div>
              </button>
            );
          })}
        </div>
      )}

      {openSlug && (
        <div className="mt-4 overflow-hidden rounded-2xl border border-white/[0.06] bg-white/[0.02]">
          <div className="flex items-center justify-between gap-3 border-b hairline px-4 py-2.5">
            <div className="flex items-center gap-2 text-sm font-semibold text-zinc-100">
              <BookOpen size={14} className="text-zinc-500" aria-hidden="true" />
              {doc?.title ?? openMeta?.title ?? openSlug}
            </div>
            <button
              type="button"
              onClick={close}
              aria-label="Close guide"
              className="grid h-7 w-7 place-items-center rounded-lg border border-white/10 bg-white/[0.04] text-zinc-400 transition-colors hover:text-zinc-100"
            >
              <X size={14} />
            </button>
          </div>
          <div className="max-h-[36rem] overflow-y-auto px-5 py-4 text-[13px] leading-relaxed text-zinc-300">
            {docLoading ? (
              <LoaderInline label="Loading guide…" />
            ) : docError ? (
              <ErrorNote>{docError}</ErrorNote>
            ) : doc ? (
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={GUIDE_MD}>
                {doc.markdown}
              </ReactMarkdown>
            ) : null}
          </div>
        </div>
      )}
    </Card>
  );
}

/* --------------------------------------------------------- troubleshooting */

// Copy kept exact with README "If something looks wrong" + install/data notes —
// these are the app's real remedies, not new ones invented for this card.
const TROUBLE: { symptom: string; fix: ReactNode }[] = [
  {
    symptom: "“Daemon offline” in the dashboard",
    fix: "Quit from the tray and relaunch — the app supervises and restarts its daemon automatically.",
  },
  {
    symptom: "“Port 8787 already in use” on launch",
    fix: "Another program (or a second Iron Jarvis) owns the port; close it and relaunch.",
  },
  {
    symptom: "Windows SmartScreen on install",
    fix: (
      <>
        Windows shows &ldquo;Windows protected your PC&rdquo; because the app isn&apos;t
        code-signed yet — click <Kbd>More info</Kbd> then <Kbd>Run anyway</Kbd>. This happens
        once per download, not every launch.
      </>
    ),
  },
  {
    symptom: "Where is my data?",
    fix: (
      <>
        In <Code>%APPDATA%\Iron Jarvis</Code> — config, the database, encrypted secrets,
        memory, and backups. It survives every update and reinstall; delete that folder for a
        full wipe.
      </>
    ),
  },
  {
    symptom: "Something else?",
    // The System health panel lives on the OVERVIEW behind the nav's Advanced
    // toggle ({advanced && ...} in app/page.tsx), and the doctor checks
    // surface in the Overview's setup card — NOT on Settings (v1.198.0 fix:
    // this entry once pointed at a Settings card that does not exist).
    fix: (
      <>
        The System health card on the{" "}
        <Link href="/" className="text-accent-soft hover:text-accent">
          Overview
        </Link>{" "}
        (switch on <span className="text-zinc-300">Advanced</span> at the bottom of the nav to
        see it) and the doctor checks in the setup card show exactly what&apos;s unhappy —
        errors are always shown honestly, never papered over.
      </>
    ),
  },
];

export default function HelpPage() {
  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="What can Iron Jarvis do?"
          subtitle="Iron Jarvis is a local-first AI operating system: you give it a goal, an agent does the work on an isolated workspace, and you stay in control by reviewing what it changes."
        />
      </Reveal>

      {/* The core loop */}
      <Reveal>
        <Card title="The core loop" icon={<Eye size={15} />}>
          <div className="grid gap-4 sm:grid-cols-3">
            {LOOP.map((step, i) => {
              const Icon = step.icon;
              return (
                <div
                  key={step.title}
                  className="relative rounded-2xl border border-white/[0.05] bg-white/[0.02] px-4 py-4"
                >
                  <div className="flex items-center gap-3">
                    <span className="grid h-9 w-9 place-items-center rounded-xl border border-accent/25 bg-accent/[0.08] text-accent-soft">
                      <Icon size={17} />
                    </span>
                    <span className="text-[11px] font-medium uppercase tracking-[0.12em] text-zinc-400">
                      Step {i + 1}
                    </span>
                  </div>
                  <div className="mt-3 text-sm font-semibold text-zinc-100">{step.title}</div>
                  <p className="mt-1 text-[13px] leading-relaxed text-zinc-500">{step.desc}</p>
                </div>
              );
            })}
          </div>
          <p className="mt-4 flex items-center gap-2 text-[12px] text-zinc-500">
            <ArrowRight size={13} className="text-accent-soft/70" />
            Ready to try it? Head to{" "}
            <Link href="/chat" className="text-accent-soft hover:text-accent">
              Chat
            </Link>{" "}
            and ask your first question.
          </p>
          <p className="mt-1.5 text-[12px] text-zinc-600">
            Want to watch an agent run step by step?{" "}
            <Link href="/sessions" className="text-zinc-500 underline decoration-white/10 underline-offset-2 hover:text-zinc-300">
              Sessions
            </Link>{" "}
            shows every run in detail.
          </p>
        </Card>
      </Reveal>

      {/* Subsystem grid */}
      <Reveal>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {SUBSYSTEMS.map((s) => {
            const Icon = s.icon;
            return (
              <Link
                key={s.href}
                href={s.href}
                className="card-surface group flex flex-col gap-3 p-5 transition-all duration-300 hover:-translate-y-0.5 hover:shadow-card-hover"
              >
                <div className="flex items-center justify-between">
                  <span className="grid h-10 w-10 place-items-center rounded-xl border border-white/[0.08] bg-white/[0.03] text-accent-soft">
                    <Icon size={19} />
                  </span>
                  <ArrowRight
                    size={15}
                    className="text-zinc-600 transition-colors group-hover:text-accent-soft"
                  />
                </div>
                <div>
                  <div className="text-sm font-semibold text-zinc-100">{s.title}</div>
                  <p className="mt-1 text-[13px] leading-relaxed text-zinc-500">{s.desc}</p>
                </div>
              </Link>
            );
          })}
        </div>
      </Reveal>

      {/* Guides — the deep docs, in-app */}
      <Reveal>
        <GuidesCard />
      </Reveal>

      {/* On your phone or another device */}
      <Reveal>
        <Card title="On your phone or another device" icon={<Smartphone size={15} />}>
          <p className="text-[13px] leading-relaxed text-zinc-400">
            Two ways to take Iron Jarvis with you: chat with it over{" "}
            <span className="text-zinc-300">Telegram</span> — the easiest by far — or open the full
            dashboard on the go.
          </p>

          {/* Easiest: a two-way destination (v1.136.0) */}
          <div className="relative mt-5 rounded-2xl border border-accent/20 bg-accent/[0.04] px-4 py-4">
            <div className="flex items-center gap-3">
              <span className="grid h-9 w-9 place-items-center rounded-xl border border-accent/25 bg-accent/[0.08] text-accent-soft">
                <MessageCircle size={17} />
              </span>
              <div>
                <div className="text-sm font-semibold text-zinc-100">
                  Chat over Telegram — a two-way destination
                </div>
                <div className="text-[11px] font-medium uppercase tracking-[0.12em] text-accent-soft/70">
                  Easiest
                </div>
              </div>
            </div>
            <p className="mt-3 text-[13px] leading-relaxed text-zinc-400">
              Text your bot like a person — same brain, same memory, and the conversation shows up
              in{" "}
              <Link href="/chat" className="text-accent-soft hover:text-accent">
                Chat
              </Link>{" "}
              on your desktop. Set it up once on the{" "}
              <Link href="/channels" className="text-accent-soft hover:text-accent">
                Notifications
              </Link>{" "}
              page:
            </p>
            <ol className="mt-3 space-y-2 text-[13px] leading-relaxed text-zinc-400">
              <li>
                <span className="text-zinc-300">1.</span> Add a{" "}
                <span className="text-zinc-200">Telegram</span> destination — create a bot with{" "}
                <Kbd>@BotFather</Kbd>, paste its token, and click{" "}
                <Kbd>Detect my chat ID</Kbd>.
              </li>
              <li>
                <span className="text-zinc-300">2.</span> Under{" "}
                <span className="text-zinc-200">Advanced</span>, turn on{" "}
                <span className="text-zinc-200">
                  &ldquo;Chat with Iron Jarvis from this destination&rdquo;
                </span>{" "}
                and add your Telegram user id to the allowed senders list.
              </li>
              <li>
                <span className="text-zinc-300">3.</span> Message your bot. Replies come back on
                your phone, and the same conversation appears in Chat here — a desktop reply goes
                out to your phone too.
              </li>
            </ol>
            <p className="mt-3 text-[12px] leading-relaxed text-zinc-500">
              Send <Code>/new</Code> to start a fresh conversation (the old one stays in your
              desktop thread list). A waiting workflow can ask you a question there too — reply
              with a number or <Code>/answer</Code>.
            </p>
            <div className="mt-3 flex items-start gap-3 rounded-2xl border border-amber-500/25 bg-amber-500/[0.07] px-4 py-3.5">
              <ShieldCheck size={18} className="mt-0.5 shrink-0 text-amber-300" aria-hidden="true" />
              <div className="text-[13px] leading-relaxed text-amber-100/90">
                <span className="font-semibold text-amber-200">
                  The allowed senders list fails closed.
                </span>{" "}
                An empty list allows nobody — only the Telegram user ids you add can talk to Iron
                Jarvis, and everyone else is silently ignored.
              </div>
            </div>
          </div>

          {/* The full dashboard on the go */}
          <p className="mt-6 text-[13px] leading-relaxed text-zinc-400">
            <span className="font-semibold text-zinc-200">The full dashboard on the go.</span>{" "}
            Iron Jarvis already runs a local web app — this dashboard — and installs as a{" "}
            <span className="text-zinc-300">PWA</span>, so it behaves like a native app on any
            device. To reach it from your phone, both devices need to be on the same network and
            the phone has to hold the same per-install token the desktop app stores. There are two
            ways to get there.
          </p>

          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            {/* Recommended: Tailscale */}
            <div className="relative rounded-2xl border border-accent/20 bg-accent/[0.04] px-4 py-4">
              <div className="flex items-center gap-3">
                <span className="grid h-9 w-9 place-items-center rounded-xl border border-accent/25 bg-accent/[0.08] text-accent-soft">
                  <Wifi size={17} />
                </span>
                <div>
                  <div className="text-sm font-semibold text-zinc-100">
                    Easy &amp; secure — a mesh VPN
                  </div>
                  <div className="text-[11px] font-medium uppercase tracking-[0.12em] text-accent-soft/70">
                    Recommended
                  </div>
                </div>
              </div>
              <ol className="mt-3 space-y-2 text-[13px] leading-relaxed text-zinc-400">
                <li>
                  <span className="text-zinc-300">1.</span> Install{" "}
                  <span className="text-zinc-200">Tailscale</span> (or a similar mesh VPN) on both
                  your PC and your phone, and sign both into the same account.
                </li>
                <li>
                  <span className="text-zinc-300">2.</span> On the PC, note its Tailscale IP — it
                  looks like <Code>100.x.y.z</Code>.
                </li>
                <li>
                  <span className="text-zinc-300">3.</span> On the phone&apos;s browser, open{" "}
                  <Code>http://100.x.y.z:8788</Code> and enter your per-install token when asked.
                </li>
              </ol>
              <p className="mt-3 text-[12px] leading-relaxed text-zinc-500">
                The VPN keeps the connection private and encrypted without exposing anything to your
                local network or the internet — no daemon settings to change.
              </p>
            </div>

            {/* Advanced: LAN allowlist */}
            <div className="relative rounded-2xl border border-white/[0.05] bg-white/[0.02] px-4 py-4">
              <div className="flex items-center gap-3">
                <span className="grid h-9 w-9 place-items-center rounded-xl border border-white/[0.08] bg-white/[0.03] text-accent-soft">
                  <KeyRound size={17} />
                </span>
                <div>
                  <div className="text-sm font-semibold text-zinc-100">
                    Direct LAN access
                  </div>
                  <div className="text-[11px] font-medium uppercase tracking-[0.12em] text-zinc-500">
                    Advanced
                  </div>
                </div>
              </div>
              <p className="mt-3 text-[13px] leading-relaxed text-zinc-400">
                The daemon binds to loopback by default for safety. To let another device in, set
                two environment variables before you start it (nothing here is applied for you —
                copy, paste, and adjust):
              </p>
              <ul className="mt-3 space-y-2 text-[13px] leading-relaxed text-zinc-400">
                <li>
                  Add your PC&apos;s LAN name/IP to the host guard —{" "}
                  <Kbd>IRONJARVIS_HOST_ALLOWLIST</Kbd>
                </li>
                <li>
                  Allow the phone&apos;s origin for the browser —{" "}
                  <Kbd>IRONJARVIS_CORS_ORIGINS</Kbd>
                </li>
              </ul>
              <pre className="mt-3 overflow-x-auto rounded-xl border border-white/[0.06] bg-black/40 p-3 font-mono text-[11px] leading-relaxed text-zinc-300">
                <span className="text-zinc-500"># set these, then restart the daemon</span>
                {"\n"}IRONJARVIS_HOST_ALLOWLIST=my-pc.local,192.168.1.42
                {"\n"}IRONJARVIS_CORS_ORIGINS=http://192.168.1.42:8788
              </pre>
              <p className="mt-3 text-[12px] leading-relaxed text-zinc-500">
                Then restart the daemon and, on the phone, open{" "}
                <Code>http://192.168.1.42:8788</Code>. The per-install token is still required — the
                desktop app stores it, but a phone has to send it too.
              </p>
            </div>
          </div>

          {/* Safety */}
          <div className="mt-4 flex items-start gap-3 rounded-2xl border border-amber-500/25 bg-amber-500/[0.07] px-4 py-3.5">
            <ShieldCheck size={18} className="mt-0.5 shrink-0 text-amber-300" aria-hidden="true" />
            <div className="text-[13px] leading-relaxed text-amber-100/90">
              <span className="font-semibold text-amber-200">Only over a trusted network.</span>{" "}
              The local daemon can run tools on your machine, so expose it only over a network or VPN
              you trust — never the open internet. When in doubt, use the mesh-VPN option above.
            </div>
          </div>
        </Card>
      </Reveal>

      {/* Troubleshooting */}
      <Reveal>
        <Card title="If something looks wrong" icon={<LifeBuoy size={15} />}>
          <ul className="space-y-3">
            {TROUBLE.map((t) => (
              <li key={t.symptom} className="border-l border-accent/20 pl-3">
                <div className="text-sm font-semibold text-zinc-100">{t.symptom}</div>
                <p className="mt-0.5 text-[13px] leading-relaxed text-zinc-500">{t.fix}</p>
              </li>
            ))}
          </ul>
        </Card>
      </Reveal>

      {/* Glossary */}
      <Reveal>
        <Card title="What the words mean" icon={<BookOpen size={15} />}>
          <p className="text-[13px] leading-relaxed text-zinc-400">
            New here? These are the terms you&apos;ll see around the app, in plain language.
          </p>
          <dl className="mt-4 grid gap-x-8 gap-y-4 sm:grid-cols-2">
            {GLOSSARY.map((g) => (
              <div key={g.term} className="border-l border-accent/20 pl-3">
                <dt className="text-sm font-semibold text-zinc-100">{g.term}</dt>
                <dd className="mt-0.5 text-[13px] leading-relaxed text-zinc-500">{g.def}</dd>
              </div>
            ))}
          </dl>
        </Card>
      </Reveal>
    </PageShell>
  );
}

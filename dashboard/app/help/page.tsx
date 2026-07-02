"use client";

import type { ReactNode } from "react";
import Link from "next/link";
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
  Sparkles,
  CheckCircle2,
  Eye,
  Smartphone,
  Wifi,
  KeyRound,
  ShieldCheck,
  BookOpen,
  type LucideIcon,
} from "lucide-react";
import { Card } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";

interface Subsystem {
  href: string;
  title: string;
  icon: LucideIcon;
  desc: string;
}

const SUBSYSTEMS: Subsystem[] = [
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
    href: "/ltm",
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
    term: "Terminals",
    def: "Real shells on your machine that agents can run commands in.",
  },
];

interface LoopStep {
  icon: LucideIcon;
  title: string;
  desc: string;
}

const LOOP: LoopStep[] = [
  {
    icon: Sparkles,
    title: "Start a session",
    desc: "Describe what you want in plain language and pick an agent.",
  },
  {
    icon: Bot,
    title: "The agent works",
    desc: "It plans, runs tools, and edits files on an isolated workspace.",
  },
  {
    icon: CheckCircle2,
    title: "Review & approve",
    desc: "Risky changes wait for your sign-off before anything lands.",
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
            <Link href="/sessions" className="text-accent-soft hover:text-accent">
              Sessions
            </Link>{" "}
            and start your first one.
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

      {/* On your phone or another device */}
      <Reveal>
        <Card title="On your phone or another device" icon={<Smartphone size={15} />}>
          <p className="text-[13px] leading-relaxed text-zinc-400">
            Iron Jarvis already runs a local web app — this dashboard — and installs as a{" "}
            <span className="text-zinc-300">PWA</span>, so it behaves like a native app on any
            device. To reach it from your phone, both devices need to be on the same network and
            the phone has to hold the same per-install token the desktop app stores. There are two
            ways to get there.
          </p>

          <div className="mt-5 grid gap-4 lg:grid-cols-2">
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

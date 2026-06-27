"use client";

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
                    <span className="text-[11px] font-medium uppercase tracking-[0.12em] text-zinc-500">
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
    </PageShell>
  );
}

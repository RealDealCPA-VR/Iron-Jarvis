// Recipe catalog for the chat empty state (v1.199.0).
//
// WHAT A RECIPE IS: a whole JOB, not an example question. The example chips
// (components/chat/examples.ts) are one-line nudges — "Summarize the files in
// a folder" — that show a single capability. A recipe is bigger: a prompt
// deliberately phrased so the model PLANS multi-step work (ask a clarifying
// question, gather, produce an artifact, report where it landed). A brand-new
// user with just a model connected can fire any of these; every step maps to
// a REAL built-in tool, reached one of two ways:
//
//  - Simple steps run right in the chat turn via auto-arm, whose candidates
//    come from AUTO_SAFE_TOOLS (tools/autoselect.py): redact_pii
//    (documents/tools.py, deterministic + offline), write_document
//    (documents/tools.py), web_search (tools/websearch.py, keyless),
//    list_files/read_file (tools/builtins.py).
//  - Standing-automation steps are NOT auto-armed — schedule_create
//    (scheduling/tools.py) and workflow_create (workflows/tools.py) are
//    absent from AUTO_SAFE_TOOLS on purpose. With Auto on, a turn that needs
//    them ESCALATES to a full agent session (the builder carries both via
//    _SELF_SERVICE_TOOLS in agents/types.py); a workflow ask may instead
//    resolve as a workflow_draft card right in chat (daemon/chat_turn.py).
//    The job completes either way, but an escalated turn mints NO doors —
//    the user watches the agent session do the work rather than getting a
//    landing-page door in the reply.
//
// No paid media keys, no shell, no setup.
//
// WHY PREFILL, NOT SEND: clicking a recipe only PREFILLS the composer — the
// user reads the prompt, edits it if they like, and presses send themselves.
// Nothing runs on click. That is the product's suggest-don't-act posture:
// the first thing a new user learns must be that this app proposes and the
// user disposes, not that tiles fire jobs behind their back.
//
// RECIPES DELIBERATELY SHOW THE PRODUCT IS BIGGER THAN CHAT: the schedule
// and workflow recipes create things that LIVE on other surfaces —
// /schedules and the workflow canvas — and their prompts ask the model to
// report what it set up so the user knows to go look. A new user who only
// ever sees the chat page never learns the rest of the app exists.
//
// The prompts intentionally have the model ASK for the folder/file/topic
// rather than assuming one — a recipe must work verbatim on a fresh install
// where we know nothing about the user's disk.

export interface Recipe {
  /** Stable identifier (used as the React key; never shown to the user). */
  key: string;
  /** Short card title. */
  title: string;
  /** One-line description of the job shown under the title. */
  blurb: string;
  /** The full prompt prefilled into the composer on click. */
  prompt: string;
}

export const RECIPES: Recipe[] = [
  {
    key: "morning-brief",
    title: "Morning brief, every day",
    blurb: "A short researched briefing, delivered on a daily schedule.",
    prompt:
      "Set up a daily schedule that runs every weekday morning: research the latest news on the web for topics I care about, then write me a short morning brief — five bullets, one takeaway line each, with links to the sources. First ask me which topics to track and what time I want it, then create the schedule and tell me what you set up so I can review it on the Schedules page.",
  },
  {
    key: "folder-cleanup",
    title: "Clean up a messy folder",
    blurb: "Inventory a folder and propose renames — nothing moves until you say so.",
    prompt:
      "Help me clean up a messy folder. Ask me which folder to look at, then list what's inside, summarize what kinds of files are in there, and propose a tidy naming scheme and folder structure as a step-by-step rename plan I can review. Don't rename or move anything until I approve the plan.",
  },
  {
    key: "redact-share",
    title: "Redact a document for sharing",
    blurb: "Find the personal info, show it, and save a privacy-safe copy.",
    prompt:
      "I need to share a document, but it contains personal information. Ask me for the file, scan it for PII — names, SSNs, account and phone numbers, addresses — and show me what you found. Then create a redacted copy saved alongside the original and tell me the exact path of the redacted file so I can share that one.",
  },
  {
    key: "research-brief",
    title: "Research and brief",
    blurb: "Web research with sources, written up as a one-page Word doc.",
    prompt:
      "Research a topic for me and turn it into a one-page brief I can hand to someone else. Ask me for the topic, gather four to six solid sources on the web, then write a Word document with a short summary up top, the key facts and numbers, and a sources section with links. Tell me exactly where you saved the document.",
  },
  {
    key: "build-workflow",
    title: "Build me a workflow",
    blurb: "Turn a repeated job into reusable steps you can run from the canvas.",
    prompt:
      "Build me a reusable workflow. Ask me what job I do repeatedly, break it into clear numbered steps, then create the workflow so I can open it on the canvas, review every step, and run it whenever I need it. When it's created, walk me through what each step does and how to run it.",
  },
  {
    key: "notes-to-email",
    title: "Turn notes into a client email",
    blurb: "Rough notes in, a polished email with a subject line out.",
    prompt:
      "Turn my rough notes into a polished email. Ask me who it's for and what I want to happen, then I'll paste the notes. Write the email with a clear subject line, a professional but warm tone, and short paragraphs, formatted as a draft I can copy straight into my email app.",
  },
];

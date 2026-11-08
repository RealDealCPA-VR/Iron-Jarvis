# Recommended Settings

*A good daily-driver setup, named the way **Settings** names it. Every row
below is a setting you can find by typing its name in the Settings search
box, or change in chat in your own words ("turn on Stable updates", "keep
this PC awake while work runs"). Each change shows what it was and offers
**Undo**.*

## The short version

| Setting | Recommended | Why |
|---|---|---|
| **Default model** | the model you really want answering (your Claude or ChatGPT plan, a local model, or a key) | A new install answers with the scripted demo until you choose one. The model menu under the chat box picks it too, and **Make this my default** saves the pick. |
| **Approvals in chat** | **Ask before commands** | It edits files and searches the web on its own and asks before running a command. **Ask before every action** asks for everything; **Accept edits** and **Auto** ask less; **Don't ask** asks only for a few safety cards. |
| **If my local model answers with an error** | **Stop and tell me (default)** | A local model that fails says so by name. Nothing else answers in its place, so the chat never leaves your network without you choosing it. |
| **Runs at once** | `2`–`3` | Caps how many agent runs work at the same time; the rest wait their turn and say so. `0` (no cap) is fine until several schedules fire together. |
| **Steps for runs you start** | `60` (default) | Room for a real piece of work. Runs that start by themselves keep a smaller limit. |
| **Ask me to approve an agent's plan before it starts** | off, unless you want to read every plan | When on, a run that writes a plan waits for your Allow before its first step. |
| **Run app tools without asking** | off (default) | Apps you connect ask before each use until you trust them. |
| **Work on my goals by itself** | off until you want it; then **Autonomy ceiling** at **Suggest only** first | Its daily limits and **Emergency stop** stay in place. |
| **Self-development** | off | Only for letting an agent change Iron Jarvis's own code, and every change waits for your review. |
| **Keep activity history (days)** | `90` (default) | How far back Activity and the change ledger go. |
| **Extra folders for file search** | the folders where your client documents live | File search and the chat's memory then reach the places your real work is. |
| **Folders Jarvis may read** | leave empty unless you want to fence reading in | A list limits what Jarvis may read to those folders (plus its own). |
| **How the assistant talks by default** | whichever voice you like | One chat can still pick another. |

## Updates, backups and power

| Setting | Recommended | Why |
|---|---|---|
| **Update channel** | **Stable (out for a day first)** on the PC you depend on | Fresh takes every release as it comes out; Stable takes a release only once it has been out a day and nothing replaced it. |
| **Backup copy folder** | a folder on another drive or a synced folder | Each backup is also copied there, so one failed disk does not take the backups with it. |
| **Keep this PC awake while work runs** | on (default) | The PC does not sleep in the middle of a long reply or a night task. |

## Coding and teams

| Setting | Recommended | Why |
|---|---|---|
| **Carry on by itself after an account's limit** | on (default) | A coding reply that hit an account's limit waits for the reset (or moves within an account group you made) and carries on. |
| **Fix my pull requests' failing checks** | on (default) | A coding session's pull request is watched; a failed check is fixed, reviewed, and only then pushed. |
| **Code editor** | **Automatic** | Opens files in VS Code when it is installed, else Cursor. |
| **Hand work to my team first** | on once you have set up a model team | The coding lead hands pieces to your team members instead of doing everything on its own model. |

## Local models (pair with "Local Models by RAM Tier")

| Setting | Recommended | Why |
|---|---|---|
| **Ollama server URL** | your Ollama address | The simplest way to use a model on this PC. Or ask in chat: "add my local Ollama model". |
| **Custom endpoint URL** | your OpenAI-compatible server (LiteLLM, vLLM, LM Studio) | For a model server Ollama does not front. |
| **Context windows** | the window of every local model you use | Iron Jarvis fits each conversation to it; an unknown window is a guess. |
| **Prefer my own hardware** | on once your local model has proven itself | Sends suitable work to your local model only after enough good results. |
| **Break big tasks into steps for local models** | on (default) | A smaller model gets a plan, then each step, then a check, instead of one long leap. |
| **Start new chats local only** | on if most of your work must stay on your network | New chats start with Local only on; you can still send one message to a cloud model when you choose. |

## Notifications

Add the built-in **This PC** destination first (nothing to set up), then
Slack, Telegram or email on **Settings › Connections › Notifications**. Choose
which alerts each destination gets — schedule results to your phone,
everything else to this PC. Adding a destination sends a test.

## Things to leave alone

- **Sandbox runtime** and the tool permissions: the defaults are the safety
  floor.
- **Conversation compaction**: the thresholds are tuned, and chat always asks
  before it summarizes while you are there.

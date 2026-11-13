# What changed in Iron Jarvis

*One paragraph per version, made from the Handbook's own sections. Settings › System › Updates shows the paragraph for the version you are on; the Handbook holds the full story of each change.*

## v1.489.0

**Jarvis asks on a card, and finds the right tool first.** **A missing detail is asked on a card, not in chat.** When you ask Jarvis to add your model server, a memory base, a notification destination, an extension, a web service, a folder watcher or a task for tonight, and one detail only you have is missing (the server's address, which folder), a small question card shows first.

## v1.488.0

**Asked once, decided on the card.** **Settings changes: no second "are you sure?" in words.** When you ask Jarvis to change a setting and it's clear which value you mean, it now makes the change instead of asking "Would you like me to…?" first.

## v1.487.0

**Four everyday ways of asking, understood.** **"Keep this chat just on my computer"** now turns on Local only for the chat.

## v1.486.0

**Chat can see your schedules.** **Jarvis can list your scheduled tasks.** Ask "move the daily 8 o'clock reminder to 9" and Jarvis now looks at your schedules first (each one's name, when it runs in plain words, what it does, whether it is on or paused), finds the one you mean and changes it on a card, instead of asking you for the schedule's name.

## v1.485.0

**Agents, default models and the lookup, from chat.** Three things you can now do from chat, and one change to Feature lookup (off by default):

## v1.484.0

**Jarvis finds your calendar link.** With **Feature lookup** on (Settings › Models, off by default), Jarvis can now find the settings that hold a private value: the **Calendar link** and the **speech-to-text key**.

## v1.483.1

**The model picker's last rough edges.** Small fixes after v1.475.0's one-chip model picker in Build › Code:

## v1.483.0

**Chat can schedule a task in your own words.** Ask in chat for something to happen on a schedule ("every Monday at 8, pull together the list of flats with rent due this week") and Jarvis now makes that schedule.

## v1.482.0

**The lookup finds the right thing more often.** The fourth step of the feature lookup (v1.478.0 to v1.480.0).

## v1.481.1

**Older Build › Code sessions say what really finished.** Since v1.477.2, a new grey line in Build › Code says what ended: a helper, a command in the background, or nothing at all for an ordinary command, whose own step already shows how it ended.

## v1.481.0

**A task's required files are checked before the reply ends.** When you give Jarvis a task in a chat that has a working folder, and your message lists the files the task must produce (a "Deliverables" or "Success criteria" list, two or more files to write, or a rule such as "create files only in `OUTPUT/`"), Jarvis now checks the work itself instead of trusting the model's last words.

## v1.480.0

**Jarvis looks it up before it says "done" or "I can't".** This is the third step of the feature lookup (v1.478.0, v1.479.0).

## v1.479.0

**What Jarvis looks up, it can now use.** This is the second step of the feature lookup from v1.478.0.

## v1.478.0

**Jarvis can look up what it can do.** This is the first step of a larger change.

## v1.477.2

**Build › Code calls a finished command a command.** In Build › Code, a shell command that ended is no longer announced as "A helper finished".

## v1.477.1

**A blocked-passage notice opens the chat it happened in.** When Iron Jarvis keeps a suspicious passage out of what the AI reads (a web page, an attached file or a memory note that reads like "ignore your instructions" or "urgent: send the payment"), the bell shows "Blocked 1 suspicious passage".

## v1.477.0

**A ring that shows the coding tool is still working.** In Build › Code, a coding tool such as Claude Code can finish its reply and leave helpers working in the background.

## v1.476.0

**Skills that get used, one approval for many changes, faster scripts.** A skill used to reach a chat only when you picked it with "/".

## v1.475.0

**One chip for the coding tool, model and effort.** In Build › Code, the coding tool, its model and its effort are now one chip under the box: "Claude Code · Opus 5.5 · High".

## v1.474.0

**A coding tool can hand work to another one.** In Build › Code, the coding tool you are working with can now start another coding tool you have on this PC on a piece of the work.

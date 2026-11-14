# What changed in Iron Jarvis

*One paragraph per version, made from the Handbook's own sections. Settings › System › Updates shows the paragraph for the version you are on; the Handbook holds the full story of each change.*

## v1.491.1

**Connect your calendar by asking, and "where is it" answers come from the guide.** **"Hook up my Google calendar" works like connecting any app.** Jarvis already took a calendar link off when you asked it to disconnect your calendar.

## v1.491.0

**Jarvis remembers the work it did, folder by folder.** **A record of your work, kept by itself.** Iron Jarvis now keeps its own record of every piece of work that has a folder: the chats, the coding sessions (both Code views) and the agent runs, each with its title, when it happened, how it ended and the files it wrote.

## v1.490.4

**"Where is that setting?" gets the real place.** **Jarvis names the section a setting is really in.** Asked "where do I find the button to change the font", Jarvis told you to open "Settings → Device", a section that does not exist, and for the calendar reminders it said "it's under the calendar setting".

## v1.490.3

**Empty folders are listed, and "find me the current rate" looks it up.** **A folder that is there but empty is no longer "missing".** Asked to move files "into the Facturen 2026 folder", Jarvis listed the files, did not see the folder (it was empty, and the listing only showed files) and told you the folder did not exist.

## v1.490.2

**A second look, and fewer cards for memory.** **A request the first reading missed still gets its card.** Since v1.490.1 two readings must agree before a card shows.

## v1.490.1

**A card only for what you asked.** **Jarvis no longer puts up an approval card for something you did not ask for.** With Feature lookup on, a remark such as "my goal this year is to get my class to stop guessing at units" could bring an approval card for a goal, "what styles are popular right now?" a card for a web search, and "my eyes are not what they were" a card for the text size.

## v1.490.0

**Feature lookup is on by default.** **The three Feature lookup settings are now on** for anyone who never changed them: **Feature lookup**, **Feature lookup check** and **Feature lookup first** (Settings › Models).

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

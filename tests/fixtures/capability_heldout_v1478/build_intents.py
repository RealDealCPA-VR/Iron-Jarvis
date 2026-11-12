"""Writes intents.json: the jobs of the held-out set and, for each, what
counts as "the right thing was reachable". Written BEFORE any score was read.

kind "tool": reached when ANY tool in `tools` is offered by the arming both
chat lanes call. kind "step": reached when page_doors offers one of `steps`
(or one of `tools`). kind "honest": reached when page_doors' not-offered
line `honest` matches (the true "Iron Jarvis does not do this").
`say` is the plain description handed to the blind writer."""
import json
import pathlib

S = ["config_set", "config_change", "config_change_protected"]
I = []


#: The schema rows that ARE each settings job (read by the index measurement,
#: tests/test_capability_index_v1478.py; the word-table measurement ignores it).
SETTING_KEYS = {
    "set_theme": ["device.theme", "device.theme_colors"],
    "set_default_model": ["default_provider", "default_model"],
    "set_approvals": ["device.approval_mode"],
    "set_text_size": ["device.text_size"],
    "set_update_channel": ["update_channel"],
    "set_backup_copy": ["backup_mirror_dir", "backup_mirror_media"],
    "set_start_with_pc": ["start_with_windows", "keep_running_in_background"],
    "set_answer_language": ["profile.language", "profile.enforce_language"],
}


def add(id, kind, say, tools=(), steps=(), honest=()):
    I.append({"id": id, "kind": kind, "say": say, "tools": list(tools),
              "steps": list(steps), "honest": list(honest),
              "settings": list(SETTING_KEYS.get(id, ()))})


# settings
add("set_theme", "tool", "change how the assistant app looks: dark or light, or its colours", S)
add("set_default_model", "tool", "choose which AI model answers from now on, in every chat", S + ["model_server_default"])
add("set_approvals", "tool", "change how often the assistant stops to ask permission before it does things", S)
add("set_text_size", "tool", "make the writing in the app bigger or smaller", S)
add("set_update_channel", "tool", "get app updates only after they have been out for a while, or get the newest ones at once", S)
add("set_backup_copy", "tool", "keep a second copy of the app's backups in another folder or on another drive", S)
add("set_start_with_pc", "tool", "have the app open by itself when the computer starts, or stop it doing that", S)
add("set_answer_language", "tool", "always answer in a certain language from now on", S + ["remember_preference", "lesson_add"])
add("read_setting", "tool", "ASK what a setting is right now, changing nothing (for example which model is the default, or whether backups are on)", ["config_list"])
# records
add("schedule_create", "tool", "have the assistant do a job again and again at set times (every morning, every Friday...)", ["schedule_create", "workflow_schedule"])
add("schedule_change", "tool", "pause, change the time of, or delete a recurring job that was set up earlier in the assistant", ["schedule_update", "schedule_delete"])
add("project_create", "tool", "start a new project in the assistant for a piece of work or a client", ["project_create"])
add("agent_create", "tool", "make a new helper agent with its own name and job", ["agent_create"])
add("agent_pause", "tool", "stop one of the helper agents from working for now, or change what it is allowed to do", ["agent_pause", "agent_update"])
add("rule_create", "tool", "a standing rule: whenever a message or email comes in, the assistant should do something by itself", ["reflex_create"])
add("watch_folder", "tool", "keep an eye on a folder on the PC and tell me when files in it change", ["sentinel_add"])
add("goal_add", "tool", "give the assistant a long-running goal it keeps working toward on its own", ["goal_add"])
add("night_task", "tool", "queue a piece of coding work to be done overnight", ["night_task_add"])
add("phone_notify", "tool", "set up messages to my phone (Telegram, Slack, Discord or email) so the assistant can reach me", ["channel_connect", "channel_toggle"])
add("app_connect", "tool", "connect an outside app to the assistant (Notion, GitHub, Google Drive, a calendar...)", ["app_connect"])
add("app_disconnect", "tool", "disconnect or switch off an outside app that was connected before", ["app_disconnect", "app_switch"])
add("local_server_add", "tool", "add my own AI model server (Ollama, LM Studio, a box on my network) so the assistant can use it", ["model_server_add"], steps=["local_model"])
add("memory_base_add", "tool", "use my notes folder or my Notion as the assistant's long-term memory", ["memory_base_add"])
add("remember_preference", "tool", "tell the assistant how I like things done, so it remembers for every later answer", ["remember_preference", "lesson_add"])
add("preference_review", "tool", "see, correct or delete what the assistant remembers about my preferences", ["preference_list", "preference_edit", "preference_forget"])
add("persona_create", "tool", "give the assistant a new personality or speaking style I can pick", ["persona_create"])
add("chat_private", "tool", "keep this one conversation on my own computer so nothing goes to the cloud", ["chat_local_only"])
add("undo_change", "tool", "see what the assistant changed, or undo its last change", ["change_undo", "changes_list", "config_undo"])
add("tool_server_add", "tool", "add a tool server (an MCP server) so the assistant gets new tools", ["mcp_server_add", "app_import"])
add("skill_save", "tool", "save the way we just did something as a reusable skill for next time", ["skill_create"])
# everyday work
add("make_file", "tool", "make a new Word, Excel or PDF file with given content", ["write_document", "excel_apply_spec", "write_file"])
add("rename_files", "tool", "rename or move files in a folder on my PC", ["rename_file", "apply_changes"])
add("web_lookup", "tool", "look something up on the internet", ["web_search", "web_fetch", "browse", "web_extract"])
add("find_file", "tool", "find a file somewhere on my computer", ["file_search", "list_files", "glob", "list_folder"])
add("arithmetic", "tool", "work out a calculation with numbers given in the message", ["calculate"])
add("make_picture", "tool", "create a picture or image from a description", ["generate_media", "pixio_generate"])
add("how_do_i", "tool", "ask how to do something in the assistant app itself (where a feature is, how it works)", ["guide_search", "guide_read", "app_search"])
add("whats_wrong", "tool", "say that the assistant app is broken or acting up and ask what is wrong", ["app_health", "run_doctor", "pack_status"])
add("find_old_chat", "tool", "find an earlier conversation with the assistant about some topic", ["history_search", "recall"])
# a page is needed
add("signin_claude_code", "step", "sign in to Claude Code (the coding program) so the assistant can use my Claude plan", steps=["signin_claude_code"])
# Label review (before any score was read): "go on Chrome and do X" is rightly
# served by offering a browser tool too; its refusal carries the pairing door.
add("browser_use", "step", "let the assistant work in my own web browser (Chrome or Edge)",
    ["browser_navigate", "browser_read_page", "browser_get_status", "browser_click", "browser_create_tab"],
    steps=["browser_addon"])
add("second_account", "step", "add a second account of the same AI subscription so work can continue when the first runs out", ["account_add"], steps=["accounts"])
add("free_local_model", "step", "set up a free AI model that runs on this PC, with nothing installed yet", ["model_server_add"], steps=["local_model"])
# not offered
add("desktop_control", "honest", "have the assistant take over the mouse and keyboard to click through a program on my desktop", honest=["desktop_control"])
add("other_computer", "honest", "open and use the assistant from another computer or phone browser in the house or office", honest=["another_computer"])
add("voice_talk", "honest", "talk to the assistant out loud instead of typing", honest=["voice_press"])
add("internet_calls_in", "honest", "let an internet service (Zapier, a website form) start a job in the assistant", honest=["internet_calls_in"])

pathlib.Path(__file__).with_name("intents.json").write_text(
    json.dumps(I, indent=1) + "\n", encoding="utf-8", newline="\n")
print(len(I), "intents")

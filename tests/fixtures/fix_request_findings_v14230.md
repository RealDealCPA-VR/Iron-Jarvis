# Fix request: Iron Jarvis coding sessions and model team

These are problems Iron Jarvis's coding sessions and model team ran into. Fix them in the Iron Jarvis repository; each problem says where it happened, what was seen, what was expected and the evidence.

- Iron Jarvis version: 1.423.0
- Operating system: Windows 11 (10.0.26200)
- Window: the last 7 days (2026-10-26 12:00 UTC to 2026-11-02 12:00 UTC)
- Covers: 2 coding sessions, 4 pieces handed to helpers or teammates, 6 reviewer findings the lead was asked to answer

## 1. The same kind of finding against one team member

- Seen 2 times (first 2026-10-31 12:10 UTC, last 2026-10-31 15:10 UTC).
- Where: a coding session's team review; who: glm-worker (custom · glm-5.3-flash); sessions: chat_b7c8d9e0, chat_a1b2c3d4.
- What happened: `2 findings about tests (missing, broken or too weak) in 2 sessions`
- What was expected: A kind of mistake a reviewer finds in a member's work in two sessions is caught by that member's team notes or its role's brief, so it stops coming back.
- Where in Iron Jarvis: `src/iron_jarvis/harness/standard.py`, `src/iron_jarvis/agents/team_findings.py`, `src/iron_jarvis/agents/model_team.py`.
- Evidence:
  - 2026-10-31 15:10 UTC · chat_b7c8d9e0 · reply chat-turn-b1 · file: `orders.py:8` · finding: `R1: no test for a refund — add a test`
  - 2026-10-31 12:10 UTC · chat_a1b2c3d4 · reply chat-turn-a1 · file: `cart.py:30` · finding: `R2: no test covers an empty cart — add one`

## 2. A reviewer's finding the lead said did not apply

- Seen 1 time (first 2026-10-31 12:10 UTC, last 2026-10-31 12:10 UTC); 1 of 6 findings by this reviewer were wrong (the lead said they did not apply).
- Where: a coding session's team review; who: opus-reviewer (claude-cli · claude-opus-5-5); sessions: chat_a1b2c3d4.
- What happened: `the total skips the tax line — the lead said it does not apply: tax is added in checkout.py, not here`
- What was expected: The reviewer checks each finding against the code and the request before it lists it, so the lead never spends a fix round on a false alarm.
- Where in Iron Jarvis: `src/iron_jarvis/harness/finish.py`, `src/iron_jarvis/harness/standard.py`, `src/iron_jarvis/agents/model_team.py`.
- Evidence:
  - 2026-10-31 12:10 UTC · chat_a1b2c3d4 · reply chat-turn-a1 · file: `cart.py:12` · finding: `R1: the total skips the tax line — add the tax` · the lead's reason: `tax is added in checkout.py, not here`

## 3. A reviewer's finding the lead never answered

- Seen 1 time (first 2026-10-31 12:10 UTC, last 2026-10-31 12:10 UTC); 1 of 6 findings the lead was asked to answer were never answered.
- Where: a coding session's team review; who: the lead (claude-cli · claude-opus-5-5); sessions: chat_a1b2c3d4.
- What happened: `no test covers an empty cart — the lead was asked to answer it and did not`
- What was expected: After a team review the lead answers every finding by its number on its own line ("R1 fixed" or "R2 does not apply: why"), as the finish check's note asks, and Iron Jarvis reads each answer.
- Where in Iron Jarvis: `src/iron_jarvis/harness/finish.py`, `src/iron_jarvis/agents/team_findings.py`, `src/iron_jarvis/harness/team.py`.
- Evidence:
  - 2026-10-31 12:10 UTC · chat_a1b2c3d4 · reply chat-turn-a1 · file: `cart.py:30` · finding: `R2: no test covers an empty cart — add one`

## 4. A finding the lead called fixed that no later piece changed

- Seen 1 time (first 2026-10-31 12:10 UTC, last 2026-10-31 12:10 UTC); 1 of 3 findings the lead said were fixed were never confirmed.
- Where: a coding session's team review; who: the lead (claude-cli · claude-opus-5-5); sessions: chat_a1b2c3d4.
- What happened: `the discount is applied twice — the lead said it was fixed; no later piece changed cart.py:40`
- What was expected: A finding the lead calls fixed is fixed by a later piece of the same reply (handed back to the worker who changed that file), so the fix is on the record.
- Where in Iron Jarvis: `src/iron_jarvis/harness/finish.py`, `src/iron_jarvis/agents/team_findings.py`, `src/iron_jarvis/harness/team.py`.
- Evidence:
  - 2026-10-31 12:10 UTC · chat_a1b2c3d4 · reply chat-turn-a1 · file: `cart.py:40` · finding: `R3: the discount is applied twice — apply it once`

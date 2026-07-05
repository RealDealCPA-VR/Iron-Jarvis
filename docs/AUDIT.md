# Iron Jarvis — File-Access, Human-Feel & Self-Correction Audit

**Scope:** can the system access and interact with all common file types (PDF,
Word, Excel, PowerPoint, CSV, TXT, MD, …); does it feel like working with an
actual person; and does it self-correct — getting better every interaction.

**Result:** all three gaps found and closed. **230 tests pass.**

---

## 1. Findings (before)

| Area | Finding |
|---|---|
| File types | Only **UTF-8 text** was supported (`read_file`/`write_file`/`edit_file`). No PDF, Word, Excel, PowerPoint, or images. The spec's `extract_pdf` / `create_document` tools were never implemented. |
| File search | Content search **skipped binary files** (cheap null-byte sniff) — so it never reached inside PDFs/Office docs. |
| Human feel | Agent system prompts were terse and mechanical ("You are the Builder agent…"). No persona, no memory of how the user likes things. |
| Self-correction | An Evaluation engine **scored** runs but nothing fed back into behavior. No feedback capture, no lessons, no prompt adaptation — the system never got better. |

---

## 2. Fixes (after)

### Documents — read & write every common type (`src/iron_jarvis/documents/`)
- **Read / extract** (`extract_text`): PDF (pypdf), Word `.docx` (python-docx, incl. tables), Excel `.xlsx` (openpyxl), PowerPoint `.pptx` (python-pptx), CSV, TXT/MD/code, and images (Pillow metadata note). Dispatched by extension; binary/unsupported raises a clear error.
- **Write / create** (`write_document`): real `.docx`, `.xlsx`, `.pptx`, `.pdf` (fpdf2), `.csv`, `.md`, `.txt`.
- **Tools** (reachable by agents): `read_document` (any path), `write_document` (workspace-scoped), `extract_pdf`. **18 round-trip tests** (write → extract, content survives) across every type.
- **File search is now document-aware**: it extracts text from PDF/Word/Excel/PowerPoint so content search reaches inside them.

### Feels like a person (`src/iron_jarvis/agents/types.py`)
- A shared **warm, human voice** for every agent — "a sharp, friendly teammate, not a faceless bot": narrates briefly, makes sensible assumptions, works with your real documents naturally, and records preferences as it learns them.

### Self-correcting learning loop (`src/iron_jarvis/learning/`)
- **Feedback → lessons:** `record_feedback(session, up/down, comment)` distills durable `LessonRecord`s; `note_preference(...)` stores high-weight user preferences.
- **Reflection:** after every session the Orchestrator calls `learning.reflect(...)`, turning outcomes into lessons.
- **The self-correction:** before each run the **Agent Runtime injects the accumulated lessons + preferences into the system prompt** (`apply_to_prompt`) — so every interaction makes the next one better. Lessons rank by weight (preference > feedback > reflection).
- **Surfaces:** daemon `POST /sessions/{id}/feedback`, `GET /lessons`; CLI `feedback` / `lessons`; dashboard feedback control + a "What I've learned" view.

---

## 3. Verification (proof)

- **Full suite: 230 passed**, 1 skipped (Windows symlink), 1 pre-existing warning.
- **Every file type round-trips** (write → extract): `docx, xlsx, pdf, pptx, csv, md, txt` all ✓.
- **Self-correction proven:** `down` feedback "keep summaries to 3 bullet points" + a preference become lessons that appear in the **next run's system prompt** (`apply_to_prompt` contains both, under a "What I've learned about working with you" header).
- **Doc-aware search proven:** searching for a word that exists only inside a `.docx` finds it.
- **Endpoints live:** `POST /documents/write` + `GET /documents/read` round-trip a Word doc; `POST /sessions/{id}/feedback` → `GET /lessons` returns the distilled lesson (weight 3).
- Tool count grew to **31** (added `read_document`, `write_document`, `extract_pdf`, `remember_preference`, `recall_lessons`).

Iron Jarvis can now open and work with your real files like a colleague would — and it gets a little better every time you use it.

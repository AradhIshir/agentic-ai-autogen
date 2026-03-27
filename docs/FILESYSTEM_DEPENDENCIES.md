# Local filesystem dependencies (inventory)

**Purpose:** Track where this repo still reads or writes **files under the project tree**. Future direction: persist and read **primarily from `db/qa_testing.db`**; this document is a **checklist only** — no behavior change implied.

**Workspace root:** Most code uses the repo root (`AgenticAIAutogen`). The agent backend may override with **`AUTOMATION_PROJECT_ROOT`** (`backend/UStoAutomationBug.py`).

---

## Canonical artifact directories (under project root)

| Path | Role |
|------|------|
| **`TestCases/`** | Manual test specs: `{JIRA_ID}_Testcase.txt` — written by TestDesigner (MCP), read by AutomationAgent, Execution/Bug flows, sync, UI, webhook. |
| **`generated_testscript/`** | Playwright specs: `Script_{JIRA_ID}_.spec.ts` — written by AutomationAgent, read by ExecutionAgent & UI. |
| **`ResultReport/`** | `execution_{JIRA_ID}.json`, `result_{JIRA_ID}.txt`, `screenshot_*.png`, `bug_*` text files — written by agents; read by `db_sync`, webhook, UI (pipeline results). |
| **`db/qa_testing.db`** | SQLite (not “artifacts” but on-disk DB file). |
| **`dashboard_config.json`** | Optional project display names for UI (not DB). |

---

## By module (Python)

### `ui/app.py`
- **`FOLDERS`:** `TestCases`, `generated_testscript`, `ResultReport`.
- **Pipeline:** runs `backend/UStoAutomationBug.py` with `cwd=PROJECT_ROOT`.
- **Filesystem reads:** `get_final_result_content`, `get_execution_summary`, `list_saved_files`; step guards `_test_cases_exist` / `_automation_script_exists` / `_execution_results_exist` (file check **before** DB).
- **Run pipeline page:** validates paths, displays testcase/script/result/bug **file** contents; lists saved paths.
- **`dashboard_config.json`:** project label overrides (with DB `projects` in `get_dashboard_data` merge).

### `backend/UStoAutomationBug.py` (primary agent entrypoint)
- Ensures dirs exist: `ResultReport`, `TestCases`, `generated_testscript`.
- Agent prompts instruct MCP **filesystem** paths for read/write of the three artifact types.
- Stub **`execution_*.json`** / **`result_*.txt`** if execute step produces no JSON.

### `db_sync.py`
- **`db_sync_testcases`:** reads `TestCases/{id}_Testcase.txt`; globs `generated_testscript/Script_{ticket_id}*.spec.ts` for automation linkage.
- **`db_sync_execution`:** reads `ResultReport/execution_{ticket_id}.json`; stores rows in DB; `report_path` string still references file path.
- **`db_sync_bugs`:** reads `ResultReport/bug_{ticket_id}_*.txt`.

### `testcase_sync.py`
- Parses **`TestCases/{jira_id}_Testcase.txt`** → upserts `user_stories`, `test_cases`, `test_case_steps`.

### `webhook/server.py`
- Runs backend with `cwd=PROJECT_ROOT`.
- Reads **`TestCases`**, **`generated_testscript`**, **`ResultReport`** for notifications / parsing (e.g. testcase lists, execution JSON, bug files).
- Uses `Path` / `open` on those paths.

### `backfill_db.py`
- Scans **`TestCases`**, **`ResultReport`**, **`generated_testscript`** to seed or repair DB.

### `ui/pages/_Login.py`
- **`db/qa_testing.db`** path (SQLite file on disk).
- Optional logo files under `ui/` (cosmetic).

### `db.py`
- Default SQLite path **`db/qa_testing.db`**; optional **`QA_DB_PATH`** env to point elsewhere (still a file).

---

## Legacy / duplicate scripts (same filesystem assumptions)

These mirror or fork the backend and reference the same folders; treat as **non-canonical** unless you still run them:

- `backend/UStoAutomationBug1.py`, `backend/UStoAutomationBug2.py`
- Root: `UStoAutomationBug.py`, `jira_test.py`, `TestCaseToAutomationToBug.py`

---

## Agents & MCP (conceptual dependency)

Execution and bug workflows assume **Playwright MCP** + **filesystem MCP** can read/write the **repo working tree**. Moving to **DB-only** will require:

- Replacing or supplementing agent `write_file` targets (e.g. blob/text columns, object storage, or generated temp files).
- Keeping or dropping on-disk Playwright spec execution (Node still needs a file path unless the runner changes).

---

## Config / dotfiles on disk

- **`.env`**, **`.streamlit/config.toml`** — local configuration (not QA artifact data).
- **`dashboard_config.json`** — UI labels.

---

*Last reviewed: inventory pass for “DB-only future”; no application code was changed for this document.*

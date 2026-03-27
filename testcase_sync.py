"""
Sync generated test case files (TestCases/<JIRA_ID>_Testcase.txt) into db/qa_testing.db
so they appear in the Test Repository. Call after TestDesigner generates test cases.
"""
import os
import re
from datetime import datetime
from pathlib import Path

# Project root
ROOT = Path(__file__).resolve().parent

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

try:
    from db import get_connection
except ImportError:
    get_connection = None  # type: ignore


def _jira_id_to_project_key(jira_id: str) -> str:
    """EC-298 -> EC."""
    return jira_id.split("-")[0].strip() if "-" in jira_id else jira_id.strip() or "?"


def parse_testcase_file(file_path: str) -> list[dict]:
    """
    Parse TestCases/<jira_id>_Testcase.txt into a list of test case dicts.
    Supports two formats:
    1. Structured: blocks starting with "Test Case ID: ..."
    2. Section+bullet: "Positive Test Cases:\n1. ...\n2. ..."
    """
    path = Path(file_path)
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    # Skip APP_URL line
    if text.startswith("APP_URL:"):
        text = text.split("\n", 1)[-1]

    # ── Format 1: structured blocks with "Test Case ID:" labels ─────────────
    # Match at line start OR after optional list marker: "- Test Case ID:", "* ...", "1. Test Case ID:"
    # (TestDesigner often emits markdown bullets before Test Case ID.)
    cases = []
    tc_block_start = re.compile(
        r"(?:^|\n)\s*(?:[-*•]\s+)?(?:\d+\.\s*)?Test\s+Case\s+ID:\s*([^\n\r]+)",
        re.IGNORECASE,
    )
    block_starts = list(tc_block_start.finditer(text))
    for i, m in enumerate(block_starts):
        tc_id = m.group(1).strip()
        start = m.end()
        end = block_starts[i + 1].start() if i + 1 < len(block_starts) else len(text)
        block = text[start:end].strip()
        tc = _parse_one_block(block)
        tc["testcase_id"] = tc.get("testcase_id") or tc_id
        if tc["testcase_id"] and (tc.get("title") or tc.get("steps")):
            cases.append(tc)
    if cases:
        return cases

    # ── Format 2: section headers + numbered bullets ─────────────────────────
    # e.g.  "Positive Test Cases:\n1. Verify login...\n2. Confirm..."
    return _parse_section_bullet_format(text, path)


def _parse_section_bullet_format(text: str, path: Path) -> list[dict]:
    """Parse 'Positive Test Cases:\n1. ...\n2. ...' style files."""
    ticket_id = path.stem.replace("_Testcase", "")
    section_map = {
        r"positive\s+test\s+cases": ("Positive", "High"),
        r"negative\s+test\s+cases": ("Negative", "Medium"),
        r"boundary\s+test\s+cases": ("Boundary", "Medium"),
        r"edge\s+test\s+cases":     ("Edge", "Low"),
    }
    cases: list[dict] = []
    current_label = None
    current_priority = "Medium"
    global_counter = 0  # single sequence across all sections: TC-001, TC-002 …

    for line in text.splitlines():
        stripped = line.strip()
        matched_section = False
        for pattern, (label, priority) in section_map.items():
            if re.match(pattern + r"\s*:", stripped, re.I):
                current_label = label
                current_priority = priority
                matched_section = True
                break
        if matched_section:
            continue
        # Do not treat structured TC metadata or "- Test Case ID:" as numbered test cases
        if re.match(r"^[-*•]\s*Test Case ID:", stripped, re.I):
            continue
        if re.match(
            r"^(Title|Test Type|Preconditions?|Test Data|Steps?|Expected Result):",
            stripped,
            re.I,
        ):
            continue
        m = re.match(r"(\d+)\.\s*(.+)", stripped)
        if m and current_label:
            global_counter += 1
            title = m.group(2).strip().rstrip(".")
            tc_id = f"{ticket_id}-TC-{global_counter:03d}"
            cases.append({
                "testcase_id": tc_id,
                "title": title,
                "description": current_label,
                "preconditions": "",
                "test_data": "",
                "steps": [],
                "expected_result": "",
                "priority": current_priority,
            })
    return cases


def _parse_one_block(block: str) -> dict | None:
    d = {
        "testcase_id": "",
        "title": "",
        "description": "",
        "preconditions": "",
        "test_data": "",
        "steps": [],
        "expected_result": "",
        "priority": "Medium",
    }
    lines = block.split("\n")
    # First line is often the test case ID (value only, e.g. EC-298-Positive-01)
    if lines and lines[0].strip() and ":" not in lines[0].strip():
        d["testcase_id"] = lines[0].strip()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if re.match(r"^Title:\s*", stripped, re.I):
            d["title"] = re.sub(r"^Title:\s*", "", stripped, flags=re.I).strip()
        elif re.match(r"^Test Type:\s*", stripped, re.I):
            d["description"] = re.sub(r"^Test Type:\s*", "", stripped, flags=re.I).strip()
            t = d["description"].lower()
            if "positive" in t:
                d["priority"] = "High"
            elif "negative" in t or "boundary" in t:
                d["priority"] = "Medium"
            else:
                d["priority"] = "Low"
        elif re.match(r"^Preconditions?:\s*", stripped, re.I):
            d["preconditions"] = re.sub(r"^Preconditions?:\s*", "", stripped, flags=re.I).strip()
        elif re.match(r"^Test Data:\s*", stripped, re.I):
            td = re.sub(r"^Test Data:\s*", "", stripped, flags=re.I).strip()
            i += 1
            while i < len(lines) and lines[i].strip() and not re.match(
                r"^(Steps?|Expected\s+[Rr]esult):", lines[i].strip(), re.I
            ):
                td += "\n" + lines[i].strip()
                i += 1
            d["test_data"] = td.strip()
            continue
        elif re.match(r"^Steps?:\s*", stripped, re.I):
            i += 1
            step_num = 0
            # New TestDesigner format: Step 1: / Action: / Expected result: (per-step)
            pending: dict | None = None  # {"num": int, "action": str, "er": str}

            def _flush_pending() -> None:
                nonlocal pending
                if not pending:
                    return
                if pending.get("action") or pending.get("er"):
                    d["steps"].append(
                        {
                            "step_number": pending["num"],
                            "step_action": (pending.get("action") or "").strip(),
                            "expected_result": (pending.get("er") or "").strip(),
                        }
                    )
                pending = None

            while i < len(lines):
                line = lines[i]
                stripped = line.strip()
                lead = len(line) - len(line.lstrip(" "))
                er_m = re.match(r"^Expected\s+[Rr]esult:\s*(.*)$", stripped, re.I)

                # Indented "Expected result:" → per-step (new format or legacy under numbered step)
                if er_m and lead >= 1:
                    step_result = er_m.group(1).strip()
                    if pending is not None:
                        pending["er"] = step_result
                        _flush_pending()
                    elif d["steps"]:
                        d["steps"][-1]["expected_result"] = step_result
                    i += 1
                    continue

                # Column 0 "Expected Result:" → summary for whole test case (end Steps section)
                if er_m and lead < 1:
                    if pending:
                        _flush_pending()
                    if not d["steps"]:
                        d["expected_result"] = er_m.group(1).strip()
                    else:
                        d["expected_result"] = er_m.group(1).strip()
                    i += 1
                    break

                if not stripped:
                    i += 1
                    continue

                step_hdr = re.match(r"^Step\s*(\d+)\s*:\s*(.*)$", stripped, re.I)
                if step_hdr:
                    _flush_pending()
                    n = int(step_hdr.group(1))
                    rest = (step_hdr.group(2) or "").strip()
                    pending = {"num": n, "action": rest, "er": ""}
                    i += 1
                    continue

                # TestDesigner format: step number alone on the line — "1:" then indented Action / Expected result
                num_colon = re.match(r"^(\d+)\s*:\s*(.*)$", stripped)
                if num_colon:
                    _flush_pending()
                    n = int(num_colon.group(1))
                    rest = (num_colon.group(2) or "").strip()
                    pending = {"num": n, "action": rest, "er": ""}
                    i += 1
                    continue

                action_m = re.match(r"^Action:\s*(.*)$", stripped, re.I)
                if action_m and pending is not None:
                    extra = action_m.group(1).strip()
                    if pending.get("action") and extra:
                        pending["action"] = (pending["action"] + " " + extra).strip()
                    elif extra:
                        pending["action"] = extra
                    i += 1
                    continue

                # Legacy numbered step: "1. ..." or "   1. ..."
                step_m = re.match(r"^(\d+)\.\s*(.+)$", stripped)
                if step_m:
                    _flush_pending()
                    step_num += 1
                    d["steps"].append(
                        {
                            "step_number": step_num,
                            "step_action": step_m.group(2).strip(),
                            "expected_result": "",
                        }
                    )
                    i += 1
                    continue

                i += 1

            _flush_pending()
            continue
        elif re.match(r"^Expected Result:\s*", stripped, re.I):
            d["expected_result"] = re.sub(r"^Expected Result:\s*", "", stripped, flags=re.I).strip()
        elif re.match(r"^Test Case ID:\s*", stripped, re.I):
            d["testcase_id"] = re.sub(r"^Test Case ID:\s*", "", stripped, flags=re.I).strip()
        i += 1
    return d


def sync_testcases_to_db(jira_id: str, project_key: str | None = None, project_name: str | None = None) -> tuple[int, str]:
    """
    Parse TestCases/<jira_id>_Testcase.txt and upsert into user_stories, test_cases, test_case_steps.
    Returns (count of test cases synced, error_message). If error_message is non-empty, count may be 0.
    """
    if get_connection is None:
        return 0, "Database connection not available (db.py)"
    jira_id = (jira_id or "").strip()
    if not jira_id:
        return 0, "jira_id is empty"
    tc_dir = ROOT / "TestCases"
    path = tc_dir / f"{jira_id}_Testcase.txt"
    if not path.is_file():
        return 0, f"File not found: {path}"
    project_key = (project_key or _jira_id_to_project_key(jira_id)).strip()
    project_name = (project_name or project_key).strip()
    cases = parse_testcase_file(str(path))
    if not cases:
        return 0, ""

    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with get_connection() as conn:
            # Ensure project exists
            conn.execute(
                "INSERT OR IGNORE INTO projects (project_key, project_name, created_date) VALUES (?, ?, ?)",
                (project_key, project_name, now),
            )
            # Ensure user story exists (minimal row)
            conn.execute(
                """INSERT OR IGNORE INTO user_stories (project_key, jira_id, title, description, normalized_story, created_date)
                   VALUES (?, ?, ?, '', '', ?)""",
                (project_key, jira_id, jira_id, now),
            )
            # Replace: delete all existing test cases for this jira_id (and their steps) in one go
            conn.execute(
                "DELETE FROM test_case_steps WHERE testcase_id IN (SELECT testcase_id FROM test_cases WHERE jira_id = ?)",
                (jira_id,),
            )
            conn.execute("DELETE FROM test_cases WHERE jira_id = ?", (jira_id,))
            # Insert test cases and steps
            for tc in cases:
                conn.execute(
                    """INSERT INTO test_cases (jira_id, testcase_id, title, description, preconditions, expected_result, test_data, priority, status, created_date)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'NOT_RUN', ?)""",
                    (
                        jira_id,
                        tc["testcase_id"],
                        tc["title"] or "",
                        tc["description"] or "",
                        tc["preconditions"] or "",
                        tc["expected_result"] or "",
                        tc["test_data"] or "",
                        tc.get("priority") or "Medium",
                        now,
                    ),
                )
                for s in tc.get("steps") or []:
                    conn.execute(
                        "INSERT INTO test_case_steps (testcase_id, step_number, step_action, expected_result) VALUES (?, ?, ?, ?)",
                        (tc["testcase_id"], s["step_number"], s["step_action"], s.get("expected_result") or ""),
                    )
            # testcase sync always inserts status NOT_RUN; restore from execution_results if present (rows survive the replace above)
            for tc in cases:
                tid = tc["testcase_id"]
                row = conn.execute(
                    "SELECT execution_status FROM execution_results WHERE testcase_id = ? ORDER BY execution_date DESC LIMIT 1",
                    (tid,),
                ).fetchone()
                if row and row[0]:
                    conn.execute(
                        "UPDATE test_cases SET status = ? WHERE testcase_id = ?",
                        (str(row[0]).strip(), tid),
                    )
        return len(cases), ""
    except Exception as e:
        return 0, str(e)


def sync_testcases_for_tickets(ticket_list: list[str], project_key: str) -> list[tuple[str, int, str]]:
    """
    Sync test case files to DB for each ticket. Returns list of (jira_id, count_synced, error).
    """
    results = []
    for jira_id in ticket_list:
        n, err = sync_testcases_to_db(jira_id, project_key=project_key)
        results.append((jira_id, n, err))
    return results

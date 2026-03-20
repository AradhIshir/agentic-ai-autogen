"""
Shared DB sync helpers – populate SQLite tables after each pipeline step.
Used by both webhook/server.py (autonomous path) and ui/app.py (manual path).
"""
import json
import logging
import re
from datetime import datetime
from pathlib import Path

log = logging.getLogger("db_sync")

# Project root = directory containing this file
PROJECT_ROOT = Path(__file__).resolve().parent

try:
    from testcase_sync import sync_testcases_to_db as _sync_tc_fn  # type: ignore
    from db import get_connection as _db_conn                        # type: ignore
    _DB_AVAILABLE = True
except ImportError as _e:
    log.warning("DB modules not importable – DB sync disabled: %s", _e)
    _DB_AVAILABLE = False
    _sync_tc_fn = None   # type: ignore
    _db_conn = None      # type: ignore


def _fuzzy_match(a: str, b: str) -> bool:
    """True if two strings share >50% of their words (case-insensitive)."""
    wa = set(re.sub(r"[^a-z0-9 ]", "", a.lower()).split())
    wb = set(re.sub(r"[^a-z0-9 ]", "", b.lower()).split())
    if not wa or not wb:
        return False
    return len(wa & wb) / min(len(wa), len(wb)) > 0.5


def db_sync_testcases(ticket_id: str) -> None:
    """Sync TestCases/<id>_Testcase.txt → test_cases, user_stories, projects tables."""
    if not _DB_AVAILABLE or _sync_tc_fn is None:
        log.warning("DB not available – skipping test case sync for %s", ticket_id)
        return
    try:
        count, err = _sync_tc_fn(ticket_id)
        if err:
            log.error("testcase_sync error for %s: %s", ticket_id, err)
        else:
            log.info("DB sync: %d test cases saved for %s", count, ticket_id)
    except Exception as exc:
        log.error("DB sync testcases failed for %s: %s", ticket_id, exc)


def db_sync_automation(ticket_id: str) -> None:
    """Link each test case row to its generated Playwright script file."""
    if not _DB_AVAILABLE or _db_conn is None:
        return
    try:
        script_dir = PROJECT_ROOT / "generated_testscript"
        scripts = list(script_dir.glob(f"Script_{ticket_id}*.spec.ts"))
        if not scripts:
            log.warning("No script files found for %s – skipping automation sync", ticket_id)
            return
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        with _db_conn() as conn:
            cur = conn.execute("SELECT testcase_id FROM test_cases WHERE jira_id = ?", (ticket_id,))
            tc_ids = [r[0] for r in cur.fetchall()]
            if not tc_ids:
                log.warning("No DB test cases for %s – skipping automation sync", ticket_id)
                return
            # All test cases map to the single combined script file
            matched = scripts[0]
            for tc_id in tc_ids:
                conn.execute("DELETE FROM automation_scripts WHERE testcase_id = ?", (tc_id,))
                conn.execute(
                    "INSERT INTO automation_scripts (testcase_id, script_name, script_path, framework, created_date) VALUES (?, ?, ?, 'playwright', ?)",
                    (tc_id, matched.name, str(matched), now),
                )
        log.info("DB sync: automation scripts linked for %s (%d test cases)", ticket_id, len(tc_ids))
    except Exception as exc:
        log.error("DB sync automation failed for %s: %s", ticket_id, exc)


def db_sync_execution(ticket_id: str) -> None:
    """Parse ResultReport/execution_<id>.json and save execution results to DB."""
    if not _DB_AVAILABLE or _db_conn is None:
        return
    try:
        exec_path = PROJECT_ROOT / "ResultReport" / f"execution_{ticket_id}.json"
        if not exec_path.is_file():
            log.warning("Execution JSON not found: %s – skipping execution sync", exec_path)
            return
        data = json.loads(exec_path.read_text(encoding="utf-8"))
        failed_details = data.get("failed_test_details", [])
        failed_titles: list[str] = [d.get("title", "").lower() for d in failed_details]
        failed_errors: dict[str, str] = {d.get("title", "").lower(): d.get("error_message", "") for d in failed_details}
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        report_ref = f"ResultReport/execution_{ticket_id}.json"
        with _db_conn() as conn:
            cur = conn.execute("SELECT testcase_id, title FROM test_cases WHERE jira_id = ?", (ticket_id,))
            tc_rows = [(r[0], r[1] or "") for r in cur.fetchall()]
            if not tc_rows:
                log.warning("No DB test cases for %s – skipping execution sync", ticket_id)
                return
            for tc_id, tc_title in tc_rows:
                # Match failed test titles by TC ID (e.g. "EC-298-TC-001" appears in title)
                # or by fuzzy title match. TC ID format is now <JIRA>-TC-NNN.
                tc_id_lower = tc_id.lower()
                tc_failed = any(
                    tc_id_lower in ft or _fuzzy_match(tc_title, ft)
                    for ft in failed_titles
                )
                exec_status = "FAILED" if tc_failed else "PASSED"
                error_msg = ""
                if tc_failed:
                    for ft in failed_titles:
                        if tc_id_lower in ft or _fuzzy_match(tc_title, ft):
                            error_msg = failed_errors.get(ft, "")
                            break
                conn.execute("DELETE FROM execution_results WHERE testcase_id = ?", (tc_id,))
                conn.execute(
                    "INSERT INTO execution_results (testcase_id, execution_status, execution_logs, report_path, execution_date) VALUES (?, ?, ?, ?, ?)",
                    (tc_id, exec_status, error_msg, report_ref, now),
                )
                conn.execute("UPDATE test_cases SET status = ? WHERE testcase_id = ?", (exec_status, tc_id))
        log.info("DB sync: execution results saved for %s (%d test cases)", ticket_id, len(tc_rows))
    except Exception as exc:
        log.error("DB sync execution failed for %s: %s", ticket_id, exc)


def db_sync_bugs(ticket_id: str) -> None:
    """Parse ResultReport/bug_<id>_*.txt files and save bugs to DB."""
    if not _DB_AVAILABLE or _db_conn is None:
        return
    try:
        result_dir = PROJECT_ROOT / "ResultReport"
        bug_files = list(result_dir.glob(f"bug_{ticket_id}_*.txt"))
        if not bug_files:
            log.info("No bug files for %s – skipping bugs sync", ticket_id)
            return
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        with _db_conn() as conn:
            cur = conn.execute("SELECT testcase_id, title FROM test_cases WHERE jira_id = ?", (ticket_id,))
            tc_rows = [(r[0], r[1] or "") for r in cur.fetchall()]
            for bug_file in bug_files:
                text = bug_file.read_text(encoding="utf-8", errors="replace")
                jira_bug_id = None
                for line in text.splitlines()[:3]:
                    m = re.match(r"JIRA_BUG_ID:\s*(.+)", line.strip(), re.I)
                    if m:
                        jira_bug_id = m.group(1).strip()
                        break
                if not jira_bug_id:
                    continue
                tc_name = ""
                for line in text.splitlines():
                    m2 = re.match(r"Test Case:\s*(.+)", line.strip(), re.I)
                    if m2:
                        tc_name = m2.group(1).strip()
                        break
                tc_name_lower = tc_name.lower()
                tc_type = next(
                    (t for t in ("Positive", "Negative", "Boundary", "Edge") if t.lower() in tc_name_lower),
                    None,
                )
                matched_tc_id = None
                if tc_type:
                    matched_tc_id = next((tid for tid, _ in tc_rows if f"-{tc_type}-" in tid), None)
                if not matched_tc_id:
                    matched_tc_id = next((tid for tid, title in tc_rows if _fuzzy_match(tc_name_lower, title)), None)
                if not matched_tc_id and tc_rows:
                    matched_tc_id = tc_rows[0][0]
                if matched_tc_id:
                    conn.execute("DELETE FROM bugs WHERE jira_bug_id = ?", (jira_bug_id,))
                    conn.execute(
                        "INSERT INTO bugs (testcase_id, jira_bug_id, bug_status, created_date) VALUES (?, ?, 'Open', ?)",
                        (matched_tc_id, jira_bug_id, now),
                    )
        log.info("DB sync: bugs saved for %s (%d bug files)", ticket_id, len(bug_files))
    except Exception as exc:
        log.error("DB sync bugs failed for %s: %s", ticket_id, exc)


def db_sync_for_step(ticket_id: str, step: str) -> None:
    """Convenience: call the right sync function(s) for a given pipeline step."""
    if step == "testcases":
        db_sync_testcases(ticket_id)
    elif step == "automation":
        db_sync_automation(ticket_id)
    elif step == "execute":
        db_sync_execution(ticket_id)
    elif step == "bugs":
        db_sync_bugs(ticket_id)
    elif step == "full":
        db_sync_testcases(ticket_id)
        db_sync_automation(ticket_id)
        db_sync_execution(ticket_id)
        db_sync_bugs(ticket_id)

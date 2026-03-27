"""
ISHIR Autonomous QA Platform – Jira Webhook Listener
=====================================================
Receives Jira "issue_updated" webhooks and triggers the QA pipeline
(TestDesigner → QALead review → AutomationAgent) whenever a User Story
moves to "In Progress".

Run:
    uvicorn webhook.server:app --host 0.0.0.0 --port 8000
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger("jira_webhook")

# ---------------------------------------------------------------------------
# Config from .env
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_SCRIPT = os.path.join(PROJECT_ROOT, "backend", "UStoAutomationBug.py")

# Make project root importable for shared modules
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from db_sync import db_sync_for_step, failed_playwright_title_matches_tc  # type: ignore
    _DB_SYNC_AVAILABLE = True
except ImportError as _import_err:
    log.warning("db_sync not importable – DB sync disabled: %s", _import_err)
    _DB_SYNC_AVAILABLE = False

    def failed_playwright_title_matches_tc(tc_id: str, tc_title: str, playwright_failed_title: str) -> bool:
        """Fallback if db_sync missing: match only by TC id substring in failure title."""
        if not playwright_failed_title or not tc_id:
            return False
        tid = re.sub(r"\s+", "", tc_id.strip().lower())
        fl = re.sub(r"\s+", "", playwright_failed_title.lower())
        return tid in fl

try:
    from testcase_sync import parse_testcase_file as _parse_testcase_file_shared  # type: ignore
except ImportError:
    _parse_testcase_file_shared = None  # type: ignore
    def db_sync_for_step(ticket_id: str, step: str) -> None:  # type: ignore
        pass

OUTLOOK_CLIENT_ID = os.environ.get("OUTLOOK_CLIENT_ID", "")
OUTLOOK_CLIENT_SECRET = os.environ.get("OUTLOOK_CLIENT_SECRET", "")
TENANT_ID = os.environ.get("TENANT_ID", "")
# Email address to send notification FROM (must have Mail.Send app permission)
NOTIFY_FROM = os.environ.get("NOTIFY_FROM", os.environ.get("JIRA_USERNAME", ""))
# Email address(es) to notify – comma-separated; defaults to same as sender
NOTIFY_TO = os.environ.get("WEBHOOK_NOTIFY_TO", NOTIFY_FROM)

# Jira status triggers
TRIGGER_STATUS_DEV  = os.environ.get("WEBHOOK_TRIGGER_STATUS", "In Progress").strip()   # → TestDesigner + AutomationAgent
TRIGGER_STATUS_QA   = os.environ.get("WEBHOOK_TRIGGER_STATUS_QA", "QA").strip()          # → ExecutionAgent + BugCreator

# Which Python interpreter to use for backend
def _python_exe() -> str:
    venv_python = os.path.join(PROJECT_ROOT, ".venv", "bin", "python3")
    if os.path.isfile(venv_python):
        return venv_python
    venv_python2 = os.path.join(PROJECT_ROOT, ".venv", "bin", "python")
    if os.path.isfile(venv_python2):
        return venv_python2
    return sys.executable


# ---------------------------------------------------------------------------
# Microsoft Graph – send email via client-credentials OAuth
# ---------------------------------------------------------------------------

async def _get_ms_token() -> str | None:
    """Obtain an MS Graph access token using client credentials flow."""
    if not all([TENANT_ID, OUTLOOK_CLIENT_ID, OUTLOOK_CLIENT_SECRET]):
        log.warning("Outlook credentials not configured – skipping email.")
        return None
    url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": OUTLOOK_CLIENT_ID,
        "client_secret": OUTLOOK_CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, data=data)
    if resp.status_code == 200:
        return resp.json().get("access_token")
    log.error("MS token error %s: %s", resp.status_code, resp.text[:200])
    return None


async def _send_qalead_notification(
    ticket_id: str,
    subject_suffix: str = "moved to In Progress – QA pipeline starting",
    body_lines: list[str] | None = None,
) -> None:
    """Send email notification to QA Lead."""
    token = await _get_ms_token()
    if not token or not NOTIFY_FROM:
        log.info("Email notification skipped (no token or sender).")
        return

    recipients = [r.strip() for r in NOTIFY_TO.split(",") if r.strip()]
    to_list = [{"emailAddress": {"address": r}} for r in recipients]

    subject = f"[ISHIR QA Platform] {ticket_id} – {subject_suffix}"
    lines_html = "".join(f"<p>{line}</p>" for line in (body_lines or []))
    body = f"<p><b>QA Lead Notification</b></p>{lines_html}<p>— ISHIR Autonomous QA Platform</p>"

    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": body},
            "toRecipients": to_list,
        },
        "saveToSentItems": "true",
    }

    url = f"https://graph.microsoft.com/v1.0/users/{NOTIFY_FROM}/sendMail"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload, headers=headers)

    if resp.status_code in (200, 202):
        log.info("Email notification sent for %s → %s", ticket_id, NOTIFY_TO)
    else:
        log.error("Email send failed %s: %s", resp.status_code, resp.text[:300])


# ---------------------------------------------------------------------------
# Email helpers – file parsers + Jira fetch
# ---------------------------------------------------------------------------

def _parse_tc_file(path: str) -> list[dict]:
    """Parse TestCases/<id>_Testcase.txt → list of {tc_id, title, test_type}.

    Uses the same parser as DB sync (testcase_sync.parse_testcase_file) so emails
    match Test Repository. Supports "- Test Case ID:" markdown and step lines.
    """
    if _parse_testcase_file_shared:
        try:
            parsed = _parse_testcase_file_shared(path)
            if parsed:
                return [
                    {
                        "tc_id": c.get("testcase_id", ""),
                        "title": c.get("title", ""),
                        "test_type": (c.get("description") or ""),
                    }
                    for c in parsed
                    if c.get("testcase_id")
                ]
        except Exception as exc:
            log.warning("Shared testcase parse failed for %s: %s", path, exc)

    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    # Legacy fallback (same patterns as testcase_sync Format 1 split)
    cases: list[dict] = []
    blocks = re.split(
        r"(?=\s*(?:[-*•]\s+)?(?:\d+\.\s*)?Test\s+Case\s+ID:)",
        text,
        flags=re.MULTILINE | re.IGNORECASE,
    )
    for block in blocks:
        tc_id = re.search(r"Test\s+Case\s+ID:\s*(.+)", block, re.I)
        title = re.search(r"Title:\s*(.+)", block, re.I)
        ttype = re.search(r"Test Type:\s*(.+)", block, re.I)
        if tc_id and tc_id.group(1).strip():
            cases.append(
                {
                    "tc_id": tc_id.group(1).strip(),
                    "title": title.group(1).strip() if title else "",
                    "test_type": ttype.group(1).strip() if ttype else "",
                }
            )
    if cases:
        return cases

    # Format 2: section + numbered lines only (not full structured TC blocks)
    ticket_id = Path(path).stem.replace("_Testcase", "")
    section_patterns = {
        r"positive\s+test\s+cases": "Positive",
        r"negative\s+test\s+cases": "Negative",
        r"boundary\s+test\s+cases": "Boundary",
        r"edge\s+test\s+cases": "Edge",
    }
    current_type: str | None = None
    global_counter = 0
    for line in text.splitlines():
        stripped = line.strip()
        matched_section = False
        for pattern, label in section_patterns.items():
            if re.match(pattern + r"\s*:", stripped, re.I):
                current_type = label
                matched_section = True
                break
        if matched_section:
            continue
        if re.match(r"^[-*•]\s*Test Case ID:", stripped, re.I):
            continue
        if re.match(
            r"^(Title|Test Type|Preconditions?|Test Data|Steps?|Expected Result):",
            stripped,
            re.I,
        ):
            continue
        m = re.match(r"(\d+)\.\s*(.+)", stripped)
        if m and current_type:
            global_counter += 1
            title = m.group(2).strip().rstrip(".")
            tc_id = f"{ticket_id}-TC-{global_counter:03d}"
            cases.append({"tc_id": tc_id, "title": title, "test_type": current_type})
    return cases


def _parse_script_file(path: str) -> list[str]:
    """Extract test() block names from a .spec.ts file."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    return re.findall(r"test\(['\"](.+?)['\"]", text)


async def _fetch_jira_fields(ticket_id: str) -> dict:
    """Return Jira issue fields dict via REST API (summary, assignee, priority, sprint)."""
    base    = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
    user    = os.environ.get("JIRA_USERNAME", "")
    token   = os.environ.get("JIRA_API_TOKEN", "")
    if not all([base, user, token]):
        return {}
    url = f"{base}/rest/api/3/issue/{ticket_id}?fields=summary,assignee,priority,customfield_10020"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, auth=(user, token))
        if resp.status_code == 200:
            return resp.json().get("fields", {})
        log.warning("Jira fetch %s: HTTP %s", ticket_id, resp.status_code)
    except Exception as exc:
        log.warning("Jira fetch error: %s", exc)
    return {}


async def _send_plain_email(subject: str, body: str) -> None:
    """Send a plain-text email via MS Graph client-credentials flow."""
    token = await _get_ms_token()
    if not token or not NOTIFY_FROM:
        log.info("Email skipped – no MS token or sender.")
        return
    recipients = [r.strip() for r in NOTIFY_TO.split(",") if r.strip()]
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": r}} for r in recipients],
        },
        "saveToSentItems": "true",
    }
    url     = f"https://graph.microsoft.com/v1.0/users/{NOTIFY_FROM}/sendMail"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload, headers=headers)
    if resp.status_code in (200, 202):
        log.info("Email sent → %s", NOTIFY_TO)
    else:
        log.error("Email send failed %s: %s", resp.status_code, resp.text[:300])


async def _send_email1(ticket_id: str) -> None:
    """Email 1: Testcase & script Complete — sent after TestDesigner + AutomationAgent finish."""
    log.info("Composing Email 1 for %s …", ticket_id)
    fields      = await _fetch_jira_fields(ticket_id)
    summary     = fields.get("summary", ticket_id)
    assignee    = (fields.get("assignee") or {}).get("displayName", "—")
    priority    = (fields.get("priority") or {}).get("name", "—")
    sprint_name = "—"
    for s in (fields.get("customfield_10020") or []):
        if isinstance(s, dict) and s.get("state") == "active":
            sprint_name = s.get("name", "—")
            break

    tc_path     = os.path.join(PROJECT_ROOT, "TestCases", f"{ticket_id}_Testcase.txt")
    script_path = os.path.join(PROJECT_ROOT, "generated_testscript", f"Script_{ticket_id}_.spec.ts")
    tc_list     = _parse_tc_file(tc_path)
    scripts     = _parse_script_file(script_path)

    tc_bullets = "\n".join(
        f"  • {tc['tc_id']} | {tc['title']} | {tc['test_type']}" for tc in tc_list
    ) or "  (no test cases found)"
    script_bullets = "\n".join(f"  • {n}" for n in scripts) or "  (no scripts found)"

    subject = f"✅ Testcase & script Complete — {ticket_id}: {summary}"
    body = f"""Hi Team,

The Test Case & Script Creation has completed successfully for the following User Story:

─────────────────────────────────
📋 USER STORY
Story ID   : {ticket_id}
Story Name : {summary}
Assignee   : {assignee}
Priority   : {priority}
Sprint     : {sprint_name}
─────────────────────────────────

🧪 TEST CASES GENERATED ({len(tc_list)} total)

{tc_bullets}

─────────────────────────────────

🤖 PLAYWRIGHT SCRIPTS GENERATED ({len(scripts)} total)

{script_bullets}

─────────────────────────────────
ISHIR QA Automation Platform
Generated automatically by TestDesigner + AutomationAgent
Do not reply to this email."""

    await _send_plain_email(subject, body)
    log.info("Email 1 sent for %s", ticket_id)


async def _send_email2(ticket_id: str) -> None:
    """Email 2: QA Execution Complete — sent after ExecutionAgent + BugCreator finish."""
    log.info("Composing Email 2 for %s …", ticket_id)
    fields  = await _fetch_jira_fields(ticket_id)
    summary = fields.get("summary", ticket_id)

    # Read execution JSON
    exec_path = os.path.join(PROJECT_ROOT, "ResultReport", f"execution_{ticket_id}.json")
    exec_data: dict = {}
    try:
        with open(exec_path, "r", encoding="utf-8") as f:
            exec_data = json.load(f)
    except Exception:
        log.warning("Could not read %s", exec_path)

    total     = exec_data.get("total_tests", 0)
    passed    = exec_data.get("passed_tests", 0)
    failed    = exec_data.get("failed_tests", 0)
    skipped   = max(0, total - passed - failed)
    pass_rate = round(passed / total * 100) if total > 0 else 0
    failed_details_list = exec_data.get("failed_test_details", []) or []
    failed_title_strings = [d.get("title", "") for d in failed_details_list if d.get("title")]

    # TC file for ID mapping
    tc_path = os.path.join(PROJECT_ROOT, "TestCases", f"{ticket_id}_Testcase.txt")
    tc_list = _parse_tc_file(tc_path)

    # Bug files: first line = "JIRA_BUG_ID: EC-312"
    result_dir = os.path.join(PROJECT_ROOT, "ResultReport")
    bug_map: dict[str, str] = {}   # filename_stem → jira_bug_key
    try:
        for fname in os.listdir(result_dir):
            if fname.startswith(f"bug_{ticket_id}_") and fname.endswith(".txt"):
                with open(os.path.join(result_dir, fname), "r", encoding="utf-8", errors="replace") as f:
                    first = f.readline().strip()
                if first.startswith("JIRA_BUG_ID:"):
                    stem = fname[len(f"bug_{ticket_id}_"):-4].lower()
                    bug_map[stem] = first.split(":", 1)[1].strip()
    except Exception:
        pass

    def _find_bug(title: str) -> str:
        tl = title.lower()
        for stem, key in bug_map.items():
            if tl in stem or stem in tl:
                return key
        return "—"

    # Build test-results table (match Playwright failure titles to manual TC via TC id + safe rules — see db_sync)
    rows = ""
    for tc in tc_list:
        is_fail = any(
            failed_playwright_title_matches_tc(tc["tc_id"], tc["title"], ft)
            for ft in failed_title_strings
        )
        result    = "❌ Fail" if is_fail else "✅ Pass"
        bug_id    = _find_bug(tc["title"]) if is_fail else "—"
        rows += f"  {ticket_id:<8} | {tc['tc_id']:<20} | {tc['title'][:42]:<42} | {result:<8} | {bug_id}\n"

    # Build bugs table
    if bug_map:
        bug_rows = ""
        for stem, key in bug_map.items():
            linked = next((tc["tc_id"] for tc in tc_list if tc["title"].lower() in stem or stem in tc["title"].lower()), "—")
            title  = stem.replace("_", " ").title()[:42]
            bug_rows += f"  {key:<8} | {linked:<20} | {title:<42} | High     | Open\n"
    else:
        bug_rows = "  No bugs raised.\n"

    subject = (
        f"✅ QA Execution Complete — {ticket_id}: {summary} | "
        f"{passed} Passed | {failed} Failed | {len(bug_map)} Bugs Raised"
    )
    body = f"""Hi Team,

The QA execution and bug creation pipeline has completed for the following User Story.
Please find the consolidated test execution report below.

═══════════════════════════════════════
📋 USER STORY
═══════════════════════════════════════
Story ID   : {ticket_id}
Story Name : {summary}

═══════════════════════════════════════
📊 EXECUTION SUMMARY
═══════════════════════════════════════
Total Test Cases : {total}
✅ Passed         : {passed}
❌ Failed         : {failed}
⏭️ Skipped        : {skipped}
Pass Rate        : {pass_rate}%

═══════════════════════════════════════
📋 TEST EXECUTION RESULTS
═══════════════════════════════════════

  US ID    | TC ID                | Test Case Name                           | Result   | Bug ID
  ---------|----------------------|------------------------------------------|----------|----------
{rows}
═══════════════════════════════════════
🐛 BUGS RAISED
═══════════════════════════════════════

  Bug ID   | Linked TC            | Bug Title                                | Severity | Status
  ---------|----------------------|------------------------------------------|----------|--------
{bug_rows}
═══════════════════════════════════════
ISHIR QA Automation Platform
Pipeline: ExecutionAgent → BugCreator → QA Lead
This email was generated automatically. Do not reply.
═══════════════════════════════════════"""

    await _send_plain_email(subject, body)
    log.info("Email 2 sent for %s", ticket_id)


# DB sync functions are in db_sync.py (shared with ui/app.py)


# ---------------------------------------------------------------------------
# Pipeline trigger
# ---------------------------------------------------------------------------

def _run_step(ticket_id: str, step: str) -> int:
    """Run one pipeline step synchronously; returns exit code."""
    env = os.environ.copy()
    env["JIRA_TICKET_ID"] = ticket_id
    env["PIPELINE_STEP"] = step

    # Ensure HOME is set so mcp-remote can locate its OAuth token store (~/.mcp-remote/)
    if "HOME" not in env:
        import pwd
        env["HOME"] = pwd.getpwuid(os.getuid()).pw_dir

    # Ensure npx / node are on PATH (needed by MCP stdio servers)
    node_paths = [
        "/usr/local/bin",
        "/opt/homebrew/bin",
        "/usr/bin",
        "/bin",
    ]
    current_path = env.get("PATH", "")
    for p in node_paths:
        if p not in current_path:
            env["PATH"] = p + ":" + env["PATH"]

    python_exe = _python_exe()
    log.info("Starting pipeline step=%s for ticket=%s", step, ticket_id)
    proc = subprocess.Popen(
        [python_exe, BACKEND_SCRIPT],
        cwd=PROJECT_ROOT,
        stdin=subprocess.DEVNULL,   # prevent stdin inheritance from uvicorn
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    # Stream output to our log
    if proc.stdout is None:
        proc.wait()
        return proc.returncode
    for line in proc.stdout:
        log.info("[%s/%s] %s", ticket_id, step, line.rstrip())
    proc.wait()
    log.info("Pipeline step=%s ticket=%s exited with code %s", step, ticket_id, proc.returncode)
    return proc.returncode


async def _trigger_dev_pipeline(ticket_id: str) -> None:
    """
    Triggered when US → 'In Progress'.
    1. Email QALead notification.
    2. TestDesigner generates test cases.
    3. AutomationAgent writes Playwright scripts.
    Execution and Bug steps wait until US moves to 'QA'.
    """
    log.info("=== [In Progress] Webhook pipeline triggered for %s ===", ticket_id)
    await _send_qalead_notification(
        ticket_id,
        subject_suffix="moved to In Progress – QA pipeline starting",
        body_lines=[
            f"Jira ticket <b>{ticket_id}</b> has moved to <b>In Progress</b>.",
            "The ISHIR Autonomous QA Platform has automatically triggered:",
            "<ul><li>TestDesigner → generating test cases</li>"
            "<li>AutomationAgent → writing Playwright scripts</li>"
            "<li>Execution will start automatically when the ticket moves to <b>QA</b>.</li></ul>",
        ],
    )
    loop = asyncio.get_running_loop()

    rc_tc = await loop.run_in_executor(None, _run_step, ticket_id, "testcases")
    if rc_tc != 0:
        log.error("testcases step failed (rc=%s) for %s – aborting automation step.", rc_tc, ticket_id)
        return
    await loop.run_in_executor(None, db_sync_for_step, ticket_id, "testcases")

    tc_path = Path(PROJECT_ROOT) / "TestCases" / f"{ticket_id}_Testcase.txt"
    if not tc_path.is_file() or tc_path.stat().st_size < 80:
        log.error(
            "testcase file missing or empty after testcases step (%s) – aborting automation for %s.",
            tc_path,
            ticket_id,
        )
        return

    rc_auto = await loop.run_in_executor(None, _run_step, ticket_id, "automation")
    if rc_auto != 0:
        log.error("automation step failed (rc=%s) for %s.", rc_auto, ticket_id)
        return
    await loop.run_in_executor(None, db_sync_for_step, ticket_id, "automation")

    await _send_email1(ticket_id)
    log.info("=== [In Progress] Pipeline complete for %s: test cases + scripts ready ===", ticket_id)


async def _trigger_qa_pipeline(ticket_id: str) -> None:
    """
    Triggered when US → 'QA'.
    1. Email QALead: app ready for testing.
    2. ExecutionAgent runs Playwright tests, saves results to ResultReport.
    3. BugCreator reads results, saves results to ResultReport. and creates Jira bugs for failures.
    QALead's system_message handles the summary email after BugCreator completes.
    """
    log.info("=== [QA] Webhook pipeline triggered for %s ===", ticket_id)
    await _send_qalead_notification(
        ticket_id,
        subject_suffix="moved to QA – starting test execution",
        body_lines=[
            f"Jira ticket <b>{ticket_id}</b> has moved to <b>QA</b>.",
            "The ISHIR Autonomous QA Platform has automatically triggered:",
            "<ul><li>ExecutionAgent → running Playwright tests</li>"
            "<li>BugCreator → analysing results and creating Jira bugs for failures</li>"
            "<li>QA Lead will receive a summary email once the pipeline completes.</li></ul>",
        ],
    )
    loop = asyncio.get_running_loop()

    rc_exec = await loop.run_in_executor(None, _run_step, ticket_id, "execute")
    if rc_exec != 0:
        log.error("execute step failed (rc=%s) for %s – aborting bug creation.", rc_exec, ticket_id)
        return
    await loop.run_in_executor(None, db_sync_for_step, ticket_id, "execute")

    rc_bugs = await loop.run_in_executor(None, _run_step, ticket_id, "bugs")
    if rc_bugs != 0:
        log.error("bugs step failed (rc=%s) for %s.", rc_bugs, ticket_id)
        return
    await loop.run_in_executor(None, db_sync_for_step, ticket_id, "bugs")

    await _send_email2(ticket_id)
    log.info("=== [QA] Pipeline complete for %s: execution done + bugs created ===", ticket_id)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("ISHIR QA Webhook Server starting – listening for Jira events.")
    log.info("PROJECT_ROOT: %s", PROJECT_ROOT)
    log.info("BACKEND_SCRIPT: %s", BACKEND_SCRIPT)
    log.info("Trigger [Dev]:  '%s'  →  testcases + automation", TRIGGER_STATUS_DEV)
    log.info("Trigger [QA]:   '%s'  →  execution + bug creation", TRIGGER_STATUS_QA)
    yield
    log.info("ISHIR QA Webhook Server shutting down.")


app = FastAPI(
    title="ISHIR QA Webhook Listener",
    description="Receives Jira webhooks and triggers the ISHIR Autonomous QA pipeline.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    """Health check – Jira can call this to verify the URL is live."""
    return {"status": "ok", "service": "ISHIR QA Webhook Listener"}


@app.post("/jira-webhook", status_code=status.HTTP_202_ACCEPTED)
async def jira_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Jira webhook endpoint.
    Routes status transitions to the correct pipeline:
      In Progress → TestDesigner + AutomationAgent
      QA          → ExecutionAgent + BugCreator + summary email
    Returns 202 immediately; pipeline runs in background.
    """
    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event = payload.get("webhookEvent", "")
    if event != "jira:issue_updated":
        return JSONResponse({"accepted": False, "reason": f"event '{event}' not handled"})

    changelog = payload.get("changelog", {})
    items = changelog.get("items", [])

    # Find any status change in this event
    status_change = next(
        (i for i in items if i.get("field") == "status"),
        None,
    )
    if not status_change:
        return JSONResponse({"accepted": False, "reason": "no status change found in changelog"})

    to_status: str = status_change.get("toString", "")
    from_status: str = status_change.get("fromString", "?")

    # Only act on our two trigger statuses
    if to_status not in (TRIGGER_STATUS_DEV, TRIGGER_STATUS_QA):
        return JSONResponse({
            "accepted": False,
            "reason": f"transition to '{to_status}' is not a handled trigger",
        })

    issue = payload.get("issue", {})
    ticket_id: str = issue.get("key", "")
    if not ticket_id:
        raise HTTPException(status_code=400, detail="issue.key missing in payload")

    issue_type = issue.get("fields", {}).get("issuetype", {}).get("name", "")
    summary = issue.get("fields", {}).get("summary", "")

    log.info(
        "Webhook accepted: %s (%s) '%s' transitioned %s → %s",
        ticket_id, issue_type, summary[:60], from_status, to_status,
    )

    if to_status == TRIGGER_STATUS_DEV:
        background_tasks.add_task(_trigger_dev_pipeline, ticket_id)
        pipeline_label = "testcases + automation (background)"
    else:
        background_tasks.add_task(_trigger_qa_pipeline, ticket_id)
        pipeline_label = "execution + bug creation (background)"

    return JSONResponse(
        status_code=202,
        content={
            "accepted": True,
            "ticket": ticket_id,
            "transition": f"{from_status} → {to_status}",
            "pipeline": pipeline_label,
        },
    )

import os
import sys
import asyncio
import json
import requests

from dotenv import load_dotenv

from autogen_agentchat.agents import AssistantAgent, UserProxyAgent
from autogen_agentchat.conditions import TextMentionTermination, MaxMessageTermination
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.ui import Console

from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_ext.tools.mcp import StdioServerParams, McpWorkbench

load_dotenv()


def _ensure_stdio_blocking() -> None:
    """Piped stdout/stderr (e.g. webhook subprocess) may be non-blocking; autogen Console then raises BlockingIOError."""
    for stream in (sys.stdout, sys.stderr):
        try:
            fd = stream.fileno()
        except (OSError, AttributeError, ValueError):
            continue
        try:
            os.set_blocking(fd, True)
        except (OSError, AttributeError, ValueError):
            pass


def _automation_project_root() -> str:
    """Workspace root for filesystem MCP and ResultReport/TestCases mkdirs."""
    default = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    env = (os.environ.get("AUTOMATION_PROJECT_ROOT") or "").strip()
    return env if env else default


def _playwright_mcp_server_params() -> StdioServerParams:
    """Playwright MCP stdio server. Headless + isolated avoid common subprocess failures (no DISPLAY, profile lock)."""
    args: list[str] = ["-y", "@playwright/mcp@latest"]
    # Default headless=true so subprocess/webhook runs work without a GUI session.
    _hl = os.environ.get("PLAYWRIGHT_MCP_HEADLESS", "true").strip().lower()
    if _hl in ("1", "true", "yes", "on"):
        args.append("--headless")
    # Default isolated=true avoids "Browser is already in use for .../mcp-chrome" when another Playwright MCP
    # (e.g. Cursor) or a previous run still holds the shared persistent profile under ~/Library/Caches/ms-playwright/.
    _iso = os.environ.get("PLAYWRIGHT_MCP_ISOLATED", "true").strip().lower()
    if _iso in ("1", "true", "yes", "on"):
        args.append("--isolated")
    _ns = os.environ.get("PLAYWRIGHT_MCP_NO_SANDBOX", "").strip().lower()
    if _ns in ("1", "true", "yes", "on"):
        args.append("--no-sandbox")
    # Optional: chrome | firefox | webkit | msedge (see `npx @playwright/mcp@latest --help`). Unset = MCP default.
    _br = os.environ.get("PLAYWRIGHT_MCP_BROWSER", "").strip()
    if _br:
        args.extend(["--browser", _br])
    # MCP defaults --timeout-action to 5000ms; locator waits in ExecutionAgent then hit TimeoutError. Override via env.
    _action_ms = (os.environ.get("PLAYWRIGHT_MCP_TIMEOUT_ACTION_MS") or "20000").strip()
    if _action_ms.isdigit():
        args.extend(["--timeout-action", _action_ms])
    return StdioServerParams(command="npx", args=args, read_timeout_seconds=300)


async def main():
    _ensure_stdio_blocking()

    model_client = OpenAIChatCompletionClient(
        model="gpt-4o-mini",
        api_key=os.environ["OPENAI_API_KEY"]
    )

    os.environ["ATLASSIAN_EMAIL"] = os.getenv("JIRA_USERNAME")
    os.environ["ATLASSIAN_API_TOKEN"] = os.getenv("JIRA_API_TOKEN")

    atlassian_server = StdioServerParams(
        command="npx",
        args=["-y", "mcp-remote", "https://mcp.atlassian.com/v1/sse"],
        read_timeout_seconds=300
    )

    playwright_server = _playwright_mcp_server_params()
    print(
        f"Playwright MCP: npx {' '.join(playwright_server.args)}",
        flush=True,
    )

    _fs_root = _automation_project_root()
    print(f"Filesystem MCP root: {_fs_root}", flush=True)
    filesystem_server = StdioServerParams(
        command="npx",
        args=[
            "-y",
            "@modelcontextprotocol/server-filesystem",
            _fs_root,
        ],
        read_timeout_seconds=120
    )

    async with (
        McpWorkbench(atlassian_server) as jira_wb,
        McpWorkbench(playwright_server) as pw_wb,
        McpWorkbench(filesystem_server) as fs_wb,
    ):
        print("✅ MCP Servers Connected")
        await _run_pipeline(model_client, jira_wb, pw_wb, fs_wb)
    print("Agent finished execution")


async def _run_split_specialist_then_qalead_stop(
    *,
    specialist,
    qalead_closer,
    specialist_task: str,
    completed_phrase: str,
    jira_id: str,
    step_label: str,
) -> None:
    """
    Split pipeline steps: specialist alone until *Completed, then QALeadCloser outputs exact STOP.

    IMPORTANT (autogen-agentchat): TextMessageTermination(arg) treats arg as *agent source name*, not message text.
    Use TextMentionTermination(text, sources=[...]) so we match the completion phrase inside the message body.
    sources= avoids the user task (which repeats the same phrases) ending the run before the specialist speaks.
    """
    team_work = RoundRobinGroupChat(
        participants=[specialist],
        termination_condition=TextMentionTermination(completed_phrase, sources=[specialist.name])
        | MaxMessageTermination(120),
    )
    await Console(team_work.run_stream(task=specialist_task))

    closer_task = (
        f"Pipeline sub-step {step_label!r} for ticket {jira_id} is finished. "
        f"Output exactly the three characters STOP and nothing else — no name, no colon, no period, no markdown, no newline."
    )
    team_stop = RoundRobinGroupChat(
        participants=[qalead_closer],
        termination_condition=TextMentionTermination("STOP", sources=[qalead_closer.name])
        | MaxMessageTermination(8),
    )
    await Console(team_stop.run_stream(task=closer_task))


async def _run_pipeline(model_client, jira_wb, pw_wb, fs_wb):
    """Run the QA pipeline. Email notifications are handled by webhook/server.py."""
    jira_id = os.environ.get("JIRA_TICKET_ID", "EC-298")
    pipeline_step = (os.environ.get("PIPELINE_STEP") or "full").strip().lower()
    is_split = pipeline_step in ("testcases", "automation", "execute", "bugs")

    # Dirs must exist so filesystem MCP write_file succeeds (paths relative to AUTOMATION_PROJECT_ROOT / repo root).
    _project_root = _automation_project_root()
    os.makedirs(os.path.join(_project_root, "ResultReport"), exist_ok=True)
    os.makedirs(os.path.join(_project_root, "TestCases"), exist_ok=True)
    os.makedirs(os.path.join(_project_root, "generated_testscript"), exist_ok=True)

    # TestDesigner Jira fetch: narrow MCP/tool response to cut prompt tokens. Override with JIRA_TESTDESIGNER_ISSUE_FIELDS
    # (comma-separated) if acceptance criteria live only in customfield_* etc.
    _jira_td_fields = (
        os.environ.get("JIRA_TESTDESIGNER_ISSUE_FIELDS") or "summary,description,comment,issuetype,status"
    ).strip()

    # QALead must NOT list agents that are absent from this run (causes wrong instructions, e.g. TestDesigner during bugs-only).
    if pipeline_step == "full":
        _qalead_scope = f"""
            Full pipeline (all four agents are in this chat). Order:
            1. Instruct TestDesigner → TestCases/{jira_id}_Testcase.txt → wait for "TestDesigner Completed".
            2. Instruct AutomationAgent → generated_testscript/Script_{jira_id}_.spec.ts → wait for "AutomationAgent Completed".
            3. Instruct ExecutionAgent → ResultReport → wait for "ExecutionAgent Completed".
            4. Instruct BugCreator → Jira bugs → wait for "BugCreator Completed".
            5. Reply exactly: STOP
            """
    elif is_split:
        # Split steps use _run_split_specialist_then_qalead_stop; full QALead scope not used.
        _qalead_scope = ""
    else:
        _qalead_scope = f"""
            Unrecognized PIPELINE_STEP; follow the user task message only. (step={pipeline_step!r})
            """

    TestDesigner = AssistantAgent(
            name="TestDesigner",
            model_client=model_client,
            workbench=[jira_wb, fs_wb],
            system_message=f"""
You are a QA Test Case Design expert. 
Your goal is to produce DETAILED, automation-ready manual test cases that the AutomationAgent can 
reliably convert into Playwright scripts. Generate detailed manual test cases—never high-level validation 
statements only.

WORKFLOW

Step 1: Call the tool `searchJiraIssuesUsingJql` with a **minimal** payload so the response omits changelog, transitions, watchers, attachments, worklog, subtasks, and issue links. Use maxResults 1 and limit fields to test-design inputs only. Do **not** pass expand.
{{
  "cloudId": "67d1724f-9c51-4717-bc9a-687b0f5aacd7",
  "jql": "key = {jira_id}",
  "maxResults": 1,
  "fields": "{_jira_td_fields}"
}}
If the tool schema expects `fields` as a JSON array of strings, pass the same names as an array instead of one comma-separated string.

Step 2: Read the description, acceptance criteria, and latest comment in the User Story.
Step 3: Generate all required test categories with full structure (see below). 
Write the output file using the filesystem MCP `write_file` tool.
---
REQUIRED TEST CASE STRUCTURE
Every test case MUST contain these fields IN ORDER:
- Test Case ID (format: <JIRA>-TC-NNN e.g. {jira_id}-TC-001)
- Title
- Test Type (exactly one of: Positive / Negative / Boundary / Edge- see REQUIRED TEST CATEGORIES RULE)
- Preconditions  (once at the **top** of the test case only — see PRECONDITIONS RULE)
- Steps          (each step = action + **expected result for that step** only — see PER-STEP FORMAT)
- Expected Result (one **summary** at the end of the test case after all steps — must match the last step’s outcome)
---
PRECONDITIONS RULE (MANDATORY)

Put **Preconditions** once per test case (before Steps). Add Just one line,
RULE — PRECONDITIONS MUST BE COMPLETE AND SELF-CONTAINED:
Every test case must be independently executable from a clean 
app state. Never assume state carries over from a previous test.
If a precondition requires a specific app state, describe it 
fully in the preconditions field as a single clear statement 
that covers everything needed before the first step executes.
Examples:

  WRONG: "User is a authenticated user"
  CORRECT: "User is a authenticated user with login credentials"

The preconditions field must be specific enough that anyone reading it can set up the exact starting state without 
referring to any other test case.
Do NOT repeat preconditions under each step.

---
PER-STEP FORMAT (MANDATORY)
Under **Steps**, each step has **only**:
1) The **action** (one UI action per step)
2) The **expected result for that step** (what must be true immediately after that action — URL, element, text, error)

Use this pattern for every step:

 <n>:
  Action: <single UI action>
  Expected result: <observable outcome after this action>
- Test Data inside each step.

    FORBIDDEN
- Bare action lines with no **Expected result** for that step
- Only a final Expected Result with no per-step expected results


The **Expected Result** line at the end of the test case is still required as a short **summary** (should align with the last step’s Expected result).

---
STEP RULES (CRITICAL FOR AUTOMATION)

Each **Action** MUST be a SINGLE UI action. Use only actions such as:

- Navigate to URL
- Enter text in field
- Click button
- Select dropdown value
- Verify element visibility
- Verify error message
- Verify page navigation

FORBIDDEN: Vague actions like "Validate login works" or "Check that the form works."

EXAMPLE (Preconditions + Test Data once at top; expected result **per step** only):
Test Data:
username: standard_user
password: secret_sauce

Steps:

Step 1:
  Action: Navigate to the application URL (use APP_URL from file header only).
  Expected result: Login page loads; username field, password field, and Login button are visible.

Step 2:
  Action: Enter "standard_user" in the Username field (use Test Data).
  Expected result: Username field shows standard_user.

Step 3:
  Action: Enter "secret_sauce" in the Password field (use Test Data).
  Expected result: Password field holds the value (masked).

Step 4:
  Action: Click the Login button.
  Expected result: User is redirected to the inventory page; product list or inventory container is visible.

Expected Result: User successfully logs in and sees the inventory page with products listed.

---
REQUIRED TEST CATEGORIES RULE

You MUST generate test cases in all four categories : 

- Positive Test Cases
- Negative Test Cases
- Boundary Test Cases
- Edge Test Cases

---
APP_URL RULE (MANDATORY — DO NOT VIOLATE)

The test case file MUST start with the application URL in EXACTLY this format:

APP_URL: <application_url>

Example: APP_URL: https://www.saucedemo.com/

Rules:
- APP_URL must be the FIRST non-empty line in the file.
- APP_URL must appear ONLY ONCE in the entire document.
- Do NOT wrap the URL in quotes.
- Do NOT add any text or explanation before or after the APP_URL line.
- Do NOT repeat the URL anywhere else in the document.

---
FILE NAMING AND HANDLING

- File path MUST be: TestCases/<JIRA_TICKET_ID>_Testcase.txt
  Example: TestCases/EC-298_Testcase.txt
- Always write files under the `TestCases` folder at the workspace root.
- If the file already exists, you MUST OVERWRITE it (do not create numbered variants).
- Use the filesystem MCP `write_file` tool to write or overwrite the file.

---
TOOL USAGE RULES (MANDATORY)

You are ONLY allowed to use the filesystem MCP tool `write_file`.

You must NOT:
- create directories
- generate automation scripts
- generate JavaScript or Playwright code

Your responsibility is ONLY to generate manual test cases. The only allowed output file location is:

TestCases/<JIRA_TICKET_ID>_Testcase.txt

---
FINAL RESPONSE

When you have finished writing all test files, reply EXACTLY with (no other text):

TestDesigner Completed

Never output STOP or Proceed — only QALead uses those.
"""
            )

    AutomationAgent = AssistantAgent(
            name="AutomationAgent",
            model_client=model_client,
            workbench=[pw_wb, fs_wb],
            system_message=f"""
You are a Playwright automation expert who writes detailed scripts.

Goal: Read the manual test cases from TestCases/{jira_id}_Testcase.txt and
convert EVERY test case into executable Playwright TypeScript code.

STEP 1 — READ THE TEST CASE FILE FIRST (MANDATORY)
Use the filesystem MCP read_file tool to read TestCases/{jira_id}_Testcase.txt.
Extract every test case including: Test Case ID, Title, Test Type, Test Data, Steps, Expected Result.
Do NOT write any code until you have read and understood the entire file.

STEP 2 — COPY TEST DATA EXACTLY (CRITICAL)
Copy ALL values (usernames, passwords, URLs) from the test case file.
NEVER abbreviate or guess any test data value. If the file says password: secret_sauce, use 'secret_sauce' exactly.

Rule 9 — ERROR MESSAGE ASSERTIONS: Never copy the full error
message text from the test case file into toContainText().
The actual error displayed by the app may differ in wording,
prefix, punctuation, or sentence structure.

Instead, extract 2 to 4 unique words that identify the error
and would appear in any reasonable variation of that message:

  If expected error is about invalid credentials:
    toContainText('do not match')

  If expected error is about empty username:
    toContainText('Username is required')

  If expected error is about empty password:
    toContainText('Password is required')

  If expected error is about locked account:
    toContainText('locked out')

  If expected error is about session expiry:
    toContainText('session expired')

  If expected error is about permissions:
    toContainText('not authorized')

  If expected error is about missing fields:
    toContainText('is required')

General rules for picking the keyword:
  - Pick words from the MIDDLE of the expected error description
  - Avoid the first word — apps often add prefixes like
    "Epic sadface:", "Error:", "Warning:", "Alert:" that vary
  - Avoid punctuation at the end — period, exclamation mark
  - Avoid words that appear in other error messages on the
    same page — pick the most unique 2 to 4 words
  - Never use the full sentence from the test case file
  - When unsure, prefer a shorter match over a longer one

STEP 3 — ONE test() BLOCK PER TEST CASE (CRITICAL)
Create exactly ONE test() block for EACH test case in the file.
Title format: '<TestType>: <TC-ID> — <Title>'  e.g. 'Positive: EC-298-TC-001 — Successful Login'
Never merge test cases into one block. ExecutionAgent counts each test() as one test.

STEP 4 — CODING RULES (follow every rule below)

Rule 1 — FILE HEADER: Always start with valid TypeScript (semicolon after APP_URL line):
  import {{ test, expect }} from '@playwright/test';
  const APP_URL = 'https://...';

Rule 2 — NO HELPERS OR CONSTANTS: Do NOT declare any const, function, or arrow function outside test() blocks.
  Write all selectors inline inside each test() block only.

Rule 3 — SELECTORS: Always use these selectors in this priority order:
  Username field : [data-test="username"]
  Password field : [data-test="password"]
  Login button   : [data-test="login-button"]
  Error message  : [data-test="error"]
  Inventory list : .inventory_list

Rule 4 — INPUT FIELDS: Input elements hold a value attribute, not text content.
  To fill an input  : await page.fill('[data-test="username"]', 'the_value');
  To verify a value : await expect(page.locator('[data-test="username"]')).toHaveValue('the_value');
  NEVER use toContainText() or toHaveText() on an input element.
  NEVER call page.fill() with an empty string to clear a field — if the field should be empty, just assert toHaveValue('').

Rule 5 — ERROR MESSAGES: Use toBeVisible() then toContainText() with a partial keyword per Rule 9:
  await expect(page.locator('[data-test="error"]')).toBeVisible();
  await expect(page.locator('[data-test="error"]')).toContainText('do not match');
  Never use toHaveText() for error messages.
  Never use the full expected error sentence — always a partial keyword per Rule 9.

Rule 6 — POSITIVE TESTS (successful login): After clicking login, always verify the redirect URL and page content:
  await expect(page).toHaveURL(/.*inventory\.html/);
  await expect(page.locator('.inventory_list')).toBeVisible();

Rule 7 — EACH TEST must start with these two lines:
  await page.goto(APP_URL);
  await expect(page).toHaveURL(APP_URL);

Rule 8 — PARENTHESES (CRITICAL): Every expect() call wraps a locator() call.
  The locator() closing ) must come BEFORE the . that calls the assertion method.
  expect( opens last and ) closes last.

  CORRECT:  await expect(page.locator('[data-test="error"]')).toBeVisible();
  WRONG:    await expect(page.locator('[data-test="error"]').toBeVisible());

  After writing every expect() line, verify: does expect( open and ) close last?
  If .toBeVisible() or any assertion method appears inside the locator() parens — fix it immediately.

Rule 10 — CLOSING BRACES: Every test() block must end with }});
  After writing all assertions for a test case, always close with:
    }});
  Before writing the next test() block, confirm the previous one is closed.
  The final line of the entire file must be }}); with no trailing code after it.
  
FILE HANDLING
Write the script to: generated_testscript/Script_{jira_id}_.spec.ts
If the file already exists, OVERWRITE it. Do NOT create numbered variants.
Use the filesystem MCP write_file tool.
Do NOT execute the scripts.

ALWAYS end your response with exactly: AutomationAgent Completed
Never output STOP or Proceed — only QALead uses those.
"""
    )

    ExecutionAgent = AssistantAgent(
            name="ExecutionAgent",
            model_client=model_client,
            workbench=[pw_wb, fs_wb],
            system_message=f"""
        ## Role
        You execute the existing Playwright spec by driving the real browser through **Playwright MCP tools only**.
        Do not run `npx playwright test`, Node, or TypeScript. Do not edit the spec.

        ## Headless / host process (important)
        The Playwright MCP server is started by the host with `--headless` or headed mode — **you cannot change that from here**.
        Headless still runs a real Chromium; use `browser_navigate` and the same steps as headed. If tools fail, report the tool error text in JSON.

        ## Source of truth
        - Read once: `generated_testscript/Script_{jira_id}_.spec.ts` (filesystem `read_file`).
        - From it: `const APP_URL = '...'` (full https URL for `browser_navigate`), each `test('EXACT_TITLE', async ({{ page }}) => {{ ... }})` block = one logical test.
        - Use the **exact** string inside `test('...', ...)` as `title` in JSON. Do not use TestCases/*.txt for execution.

        ## Hard rules (pipeline + browser)
        1. **One session, one test at a time:** finish one `test()` (navigate → steps → pass/fail) before the next. No parallel browser tool calls; no second browser/window — that invalidates the run.
        2. **Per test, first browser_* call** is always `browser_navigate` with APP_URL (avoids about:blank). Then `browser_snapshot`. Do not snapshot before that navigate when starting a test.
        3. **Refs:** after each snapshot, use `ref` (and schema fields) from MCP for `browser_type`, `browser_click`, `browser_fill_form` — match the spec’s selectors (e.g. `[data-test="username"]`).
        4. **Artifacts before done:** use filesystem `write_file` to create **both** `ResultReport/execution_{jira_id}.json` and `ResultReport/result_{jira_id}.txt` **before** you say `ExecutionAgent Completed`. If the browser or a step fails, still write JSON with real `error_message` text (e.g. from tool errors or snapshot). Optional: `browser_screenshot` → `ResultReport/screenshot_{jira_id}.png` if any test failed.

        ## Spec API → MCP (never execute the .spec.ts file)
        - `page.goto` / navigation → `browser_navigate(url)`
        - `page.fill` → `browser_type` or `browser_fill_form` (after snapshot, per tool schema)
        - `page.click` → `browser_click`
        - `expect(...)` (visible, value, text, URL) → `browser_wait_for` / `browser_snapshot`; judge pass/fail from snapshot or tool output, not `expect()`

        ## Per-test loop (match the spec line-by-line)
        For each `test('TITLE', ...)` body, follow the **exact order** of statements in that block:
        1. `browser_navigate(APP_URL)` → `browser_snapshot` (fresh page for each test).
        2. Walk the body top-to-bottom: for every `page.fill` **before** the next `page.click`, perform those fills with `browser_type` / `browser_fill_form` using refs from the latest snapshot — **never click `[data-test="login-button"]` until every spec `fill` that appears above that click in the same test has been done.** Skipping a fill causes real failures (e.g. "Username is required").
        3. For tests that **intentionally** leave fields empty (only `toHaveValue('')` checks, no `fill`), do not type into those fields; snapshot, then click per spec.
        4. After click, use `browser_wait_for` / `browser_snapshot` to verify what the spec’s `expect` lines require (error banner text, URL, inventory list).
        5. Record PASS or FAIL for that TITLE (FAIL = assertion not met or tool error; copy visible error / tool text verbatim when possible).
        6. For screenshots, prefer `browser_screenshot` with a **filename** under `ResultReport/` if the tool allows, to avoid huge inline image payloads.

        ## execution_{jira_id}.json shape (write_file after **all** tests)
        {{
            "jira_ticket_id": "{jira_id}",
            "total_tests": <int>,
            "passed_tests": <int>,
            "failed_tests": <int>,
            "failed_test_details": [{{"title": "<exact test() title>", "error_message": "<string>"}}]
        }}

        ## End
        After both files are written, send the exact phrase ExecutionAgent Completed **once** — do not repeat it in later turns.
        Never output STOP or Proceed — only QALead uses those.
        """
    )

    BugCreator = AssistantAgent(
            name="BugCreator",
            model_client=model_client,
            workbench=[jira_wb, fs_wb],
            system_message="""
         ## You are a QA Bug Creation Agent. Your responsibility is to analyze Playwright execution reports and 
            create bug records for every failed test case. You do NOT execute tests. 
            You do NOT modify automation scripts. You ONLY read execution reports and create bugs.
            
            **PAY ATTENTION: VERY CRITICAL**
            Before performing any action, 
                Search folder ResultReport for files matching:
                execution_*.json
                If no such file exists:
                respond exactly with:
                REMAIN SILENT and WAIT for instructions
              

            INPUT SOURCE
            Execution reports are located in folder: ResultReport
            Files follow the format: execution_<JIRA_TICKET_ID>.json
            Example: ResultReport/execution_EC-298.json
            Each JSON file corresponds to execution of one Playwright automation script. 
            This JSON file is the ONLY source of truth for execution results.
            ----------------------------------------------------------------------------------------------------------------------------------------------
            
            PROCESS
            
            1. Locate all files matching pattern execution_<JIRA_TICKET_ID>.json inside ResultReport.
            2. Read each JSON execution report.
            3. Identify all failed test cases.
            
            4.TESTCASE STEP EXTRACTION RULE
               When a failed test case is detected from execution_<JIRA_ID>.json, 
               use the failed test title to locate the original manual test case definition stored in the TestCases folder
               Steps to follow:
            1. Read execution_<JIRA_ID>.json from the ResultReport folder.
            2. Identify the failed test title from failed_test_details. 
                Example: "Positive Test Case: Successful Login".
            3. Open files inside the TestCases folder using the filesystem MCP tool that matches the <JIRA_ID> .
            4. Search for the matching test case section where the title corresponds to the failed test case.
            Example structure inside the test case file:
            Preconditions 
            Test Data:
            username: standard_user
            password: secret_sauce

            Steps:

            Step 1:
            Action: Navigate to the application URL (use APP_URL from file header only).
            Expected result: Login page loads; username field, password field, and Login button are visible.

            Step 2:
            Action: Enter "standard_user" in the Username field (use Test Data).
            Expected result: Username field shows standard_user.

            Expected Result at the end of the test case:

            1. Extract the following information:Precondition, Steps, Expected Result
            2. Use the extracted information when generating the Jira bug description.
            JIRA BUG DESCRIPTION FORMAT , Script Name: Script_<JIRA_ID>.spec.ts, Test Case: <FAILED_TEST_TITLE>,
            Precondition, Steps to Reproduce,Expected Result,Actual Result,
            Execution failed during automated Playwright execution.
            Error Message:<error_message from execution JSON file>,
            Automation Evidence:Failure detected during automated Playwright execution.
            Screenshot:ResultReport/screenshot_<JIRA_ID>.png
            IMPORTANT RULES:Steps must come from the TestCases folder.
            Do not invent steps.If the matching test case cannot be found, fall back to generic reproduction steps.
            
            BUG CREATION RULE
            Create one bug for each failed test case.
            Use the filesystem MCP `write_file` tool to write or overwrite the file IF it already exists."
            Bug fields must be generated as follows:
            Issue Type: Bug
            Summary: <JIRA_TICKET_ID>_<FAILED_TEST_TITLE>
            Description must contain:
            Script Name: <script name>
            Test Case: <failed test title>
            Environment: Playwright Automation Execution
            Execution Time: <timestamp if available>
            Error Message: <error message>
            Expected Result: expected result from the test case file.
            Actual Result: Application produced the error captured during test execution.
            
            Example structure inside the bug logged in jira:
                Script Name: Script_EC-298.spec.ts
                Test Case: Edge Test Case: Long Username and Password
                Environment: Playwright Automation Execution
                Error Message: Timeout waiting for expected error message to be visible.
                Expected Result: Application should behave as defined in the test case.
                Steps to Reproduce:
                1.)Navigate to the relevant application page.
                2.)Perform the scenario described in the failed test case.
                3.)Observe the failure described in the error message.
                4.)Automation Evidence: Failure detected during automated Playwright execution.
               Screenshot: ResultReport/screenshot_<JIRA_TICKET_ID>.png (if available)
            
            ---
            
            JIRA ISSUE CREATION
            Use the Jira MCP tool to create the bug in Jira Cloud.
            Use the provided cloudId when calling the Jira tool.
            Example Jira MCP tool call:
            jira_create_issue({
            cloudId: "67d1724f-9c51-4717-bc9a-687b0f5aacd7",
            projectKey: "EC",
            issueType: "Bug",
            summary: "<JIRA_TICKET_ID>_<FAILED_TEST_TITLE>",
            description: `
            Script Name: <script name>
            
            Test Case:
            <FAILED_TEST_TITLE>
            
            Environment:
            Playwright Automation Execution
            
            Error Message: <error message>
            
            Steps to Reproduce:
            
            1. Navigate to the relevant application page
            2. Perform the scenario described in the test case
            3. Observe the failure described in the error message
            
            Automation Evidence:
            Failure detected during Playwright automated execution
            
            Screenshot:
            ResultReport/screenshot_<JIRA_TICKET_ID>.png
            `
            })
            --
            
            LOCAL FILE CREATION (REQUIRED — used by QALead for the summary email)
            Create a local file using filesystem MCP tool write_file.
            File location: ResultReport
            File name format:
            bug_<JIRA_TICKET_ID>_<FAILED_TEST_TITLE>.txt
            Example:
            ResultReport/bug_EC-298_Login button validation failure.txt

            CRITICAL — The VERY FIRST LINE of the file MUST be the Jira issue key
            that was returned by jira_create_issue (e.g. EC-312), in EXACTLY this format:
            JIRA_BUG_ID: EC-312

            The remainder of the file must include the bug summary and full bug description.
            -------------------------------------------------------------------
            
            CRITICAL RULES
            • Always rely only on JSON execution reports
            • Do NOT assume failures
            • Do NOT create bugs if no failed tests exist
            • Create one bug per failed test case
            • Do NOT modify execution reports
            • Do NOT modify automation scripts
            ----------------------------------
            
            FINAL RESPONSE
            When processing of all execution reports is finished respond EXACTLY with:
            BugCreator Completed
            Do NOT include any additional text.
            Never output STOP or Proceed — only QALead uses those.
        """
    )

    _qalead_critical = """
            ══════════════════════════════════════════════
            CRITICAL RULE — FULL / GENERIC PIPELINE
            ══════════════════════════════════════════════
            The initial user TASK tells you which agents to instruct and when to reply STOP.
            When the task says reply STOP — output exactly STOP with no other text (no name prefix, no colon, no period).
            Do NOT send emails. Email notifications are handled externally.
            ══════════════════════════════════════════════
        """

    QALeadCloser = None
    QALead = None
    if is_split:
        QALeadCloser = AssistantAgent(
            name="QALead",
            model_client=model_client,
            workbench=[],
            system_message="""
You are the QA pipeline closer. The specialist agent has already finished its sub-step.
When the user says the sub-step is done, your entire reply must be exactly three letters: STOP
No agent name, no colon, no period, no markdown, no spaces, no explanation — only STOP.
""",
        )
    else:
        QALead = AssistantAgent(
            name="QALead",
            model_client=model_client,
            workbench=[],
            system_message=f"""
            You are the QA Lead supervising the testing automation workflow.

            {_qalead_critical}

            SCOPE FOR THIS RUN (PIPELINE_STEP={pipeline_step}):
            {_qalead_scope}

            Each agent must only perform their own responsibility.
            Agents must NOT perform each other's responsibilities.

            """,
        )

    if pipeline_step == "testcases":
        await _run_split_specialist_then_qalead_stop(
            specialist=TestDesigner,
            qalead_closer=QALeadCloser,
            specialist_task=(
                f"Jira ticket {jira_id}. Follow your system instructions: fetch from Jira, "
                f"write_file TestCases/{jira_id}_Testcase.txt. "
                f"When done, send one TextMessage whose content is exactly: TestDesigner Completed "
                f"(no other text, never STOP or Proceed)."
            ),
            completed_phrase="TestDesigner Completed",
            jira_id=jira_id,
            step_label="testcases",
        )

    elif pipeline_step == "automation":
        await _run_split_specialist_then_qalead_stop(
            specialist=AutomationAgent,
            qalead_closer=QALeadCloser,
            specialist_task=(
                f"Jira ticket {jira_id}. Read TestCases/{jira_id}_Testcase.txt, "
                f"write generated_testscript/Script_{jira_id}_.spec.ts. "
                f"When done, send one TextMessage whose content is exactly: AutomationAgent Completed "
                f"(no other text, never STOP or Proceed)."
            ),
            completed_phrase="AutomationAgent Completed",
            jira_id=jira_id,
            step_label="automation",
        )

    elif pipeline_step == "execute":
        await _run_split_specialist_then_qalead_stop(
            specialist=ExecutionAgent,
            qalead_closer=QALeadCloser,
            specialist_task=(
                f"Jira ticket {jira_id}. "
                f"(1) read_file generated_testscript/Script_{jira_id}_.spec.ts "
                f"(2) run each test() via Playwright MCP; first browser_* per test: browser_navigate(APP_URL from script) "
                f"(3) write_file ResultReport/execution_{jira_id}.json "
                f"(4) write_file ResultReport/result_{jira_id}.txt (summary) "
                f"(5) if any test failed, browser_screenshot ResultReport/screenshot_{jira_id}.png "
                f"(6) when done, send one TextMessage whose content is exactly: ExecutionAgent Completed "
                f"(no other text, never STOP or Proceed)."
            ),
            completed_phrase="ExecutionAgent Completed",
            jira_id=jira_id,
            step_label="execute",
        )

    elif pipeline_step == "bugs":
        await _run_split_specialist_then_qalead_stop(
            specialist=BugCreator,
            qalead_closer=QALeadCloser,
            specialist_task=(
                f"Jira ticket {jira_id}. Analyze ResultReport and create Jira bugs per your instructions. "
                f"When done, send one TextMessage whose content is exactly: BugCreator Completed "
                f"(no other text, never STOP or Proceed)."
            ),
            completed_phrase="BugCreator Completed",
            jira_id=jira_id,
            step_label="bugs",
        )

    else:
        task = (
            f"Start QA automation pipeline for Jira ticket {jira_id} by instructing all agents in order: "
            f"TestDesigner → AutomationAgent → ExecutionAgent → BugCreator. "
            f"After all agents have finished, reply exactly: STOP"
        )
        termination = TextMentionTermination("STOP", sources=["QALead"]) | MaxMessageTermination(120)
        participants = [QALead, TestDesigner, AutomationAgent, ExecutionAgent, BugCreator]

        team = RoundRobinGroupChat(
            participants=participants,
            termination_condition=termination
        )

        await Console(
            team.run_stream(task=task)
        )

    # If execute step ended without agent-written artifacts, BugCreator still needs a valid execution JSON.
    if pipeline_step == "execute":
        ej = os.path.join(_project_root, "ResultReport", f"execution_{jira_id}.json")
        if not os.path.isfile(ej):
            stub = {
                "jira_ticket_id": jira_id,
                "total_tests": 0,
                "passed_tests": 0,
                "failed_tests": 0,
                "failed_test_details": [],
                "agent_note": "Stub written by pipeline: ExecutionAgent did not create execution JSON (timeout, crash, or no tool calls).",
            }
            with open(ej, "w", encoding="utf-8") as f:
                json.dump(stub, f, indent=2)
            rt = os.path.join(_project_root, "ResultReport", f"result_{jira_id}.txt")
            with open(rt, "w", encoding="utf-8") as f:
                f.write(
                    f"Jira: {jira_id}\n"
                    f"ERROR: No execution report from ExecutionAgent. Stub JSON written.\n"
                    f"Check Playwright MCP, OPENAI_API_KEY, and agent logs.\n"
                )
            print(
                f"WARNING: Wrote stub ResultReport/execution_{jira_id}.json — ExecutionAgent produced no report.",
                flush=True,
            )

   # ── Token usage summary ──────────────────────────────────────────────────
    usage = model_client.total_usage()
    prompt_tokens      = usage.prompt_tokens
    completion_tokens  = usage.completion_tokens
    total_tokens       = prompt_tokens + completion_tokens
    # gpt-4o-mini pricing: $0.15 / 1M input tokens, $0.60 / 1M output tokens
    cost_usd = (prompt_tokens * 0.15 + completion_tokens * 0.60) / 1_000_000
    print("\n" + "═" * 52)
    print(f"  TOKEN USAGE  [{pipeline_step.upper()} / {jira_id}]")
    print("═" * 52)
    print(f"  Prompt tokens     : {prompt_tokens:>10,}")
    print(f"  Completion tokens : {completion_tokens:>10,}")
    print(f"  Total tokens      : {total_tokens:>10,}")
    print(f"  Estimated cost    : ${cost_usd:>10.4f}  (gpt-4o-mini)")
    print("═" * 52 + "\n")




if __name__ == "__main__":
    asyncio.run(main())

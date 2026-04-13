import os
import sys
import asyncio
import json

from dotenv import load_dotenv

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.conditions import TextMentionTermination, MaxMessageTermination
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.ui import Console

from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_ext.tools.mcp import StdioServerParams, McpWorkbench

load_dotenv()

# PIPELINE_STEP values that run one specialist + QALead closer (not the full round-robin).
_SPLIT_PIPELINE_STEPS = frozenset({"testcases", "automation", "execute", "bugs"})


def _env_truthy(name: str, default: str = "") -> bool:
    """True if env var is set to 1/true/yes/on (case-insensitive); uses default when unset."""
    raw = os.environ.get(name, default)
    return (raw or "").strip().lower() in ("1", "true", "yes", "on")


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


def _ensure_workspace_dirs(project_root: str) -> None:
    """Ensure pipeline output dirs exist. Creates each folder only if it is missing (never replaces content)."""
    for name in ("TestCases", "generated_testscript", "ResultReport"):
        path = os.path.join(project_root, name)
        if not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)


def _playwright_mcp_server_params() -> StdioServerParams:
    """Playwright MCP stdio server. Headless + isolated avoid common subprocess failures (no DISPLAY, profile lock)."""
    args: list[str] = ["-y", "@playwright/mcp@latest"]
    # Default headless=true so subprocess/webhook runs work without a GUI session.
    if _env_truthy("PLAYWRIGHT_MCP_HEADLESS", "true"):
        args.append("--headless")
    # Default isolated=true avoids "Browser is already in use for .../mcp-chrome" when another Playwright MCP
    # (e.g. Cursor) or a previous run still holds the shared persistent profile under ~/Library/Caches/ms-playwright/.
    if _env_truthy("PLAYWRIGHT_MCP_ISOLATED", "true"):
        args.append("--isolated")
    if _env_truthy("PLAYWRIGHT_MCP_NO_SANDBOX"):
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
    _ensure_workspace_dirs(_fs_root)
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
        f"Output exactly the three characters STOP and nothing else - no name, no colon, no period, no markdown, no newline."
    )
    team_stop = RoundRobinGroupChat(
        participants=[qalead_closer],
        termination_condition=TextMentionTermination("STOP", sources=[qalead_closer.name])
        | MaxMessageTermination(8),
    )
    await Console(team_stop.run_stream(task=closer_task))


def _resolve_jira_ticket_and_cloud() -> tuple[str, str]:
    jira_id = (os.environ.get("JIRA_TICKET_ID") or os.environ.get("JIRA_TICKET_ID_DEFAULT") or "").strip()
    if not jira_id:
        raise ValueError("Set JIRA_TICKET_ID or JIRA_TICKET_ID_DEFAULT (e.g. in .env) before running the pipeline.")
    cloud_id = (os.environ.get("JIRA_CLOUD_ID") or "").strip()
    if not cloud_id:
        raise ValueError("Set JIRA_CLOUD_ID in .env (Atlassian cloud UUID for MCP Jira tools).")
    return jira_id, cloud_id


def _maybe_write_execution_stub(project_root: str, jira_id: str, pipeline_step: str) -> None:
    """If execute step left no execution JSON, write stub files so the bugs step can proceed."""
    if pipeline_step != "execute":
        return
    ej = os.path.join(project_root, "ResultReport", f"execution_{jira_id}.json")
    if os.path.isfile(ej):
        return
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
    rt = os.path.join(project_root, "ResultReport", f"result_{jira_id}.txt")
    with open(rt, "w", encoding="utf-8") as f:
        f.write(
            f"Jira: {jira_id}\n"
            f"ERROR: No execution report from ExecutionAgent. Stub JSON written.\n"
            f"Check Playwright MCP, OPENAI_API_KEY, and agent logs.\n"
        )
    print(
        f"WARNING: Wrote stub ResultReport/execution_{jira_id}.json - ExecutionAgent produced no report.",
        flush=True,
    )


def _print_token_usage_summary(model_client, pipeline_step: str, jira_id: str) -> None:
    usage = model_client.total_usage()
    prompt_tokens = usage.prompt_tokens
    completion_tokens = usage.completion_tokens
    total_tokens = prompt_tokens + completion_tokens
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


async def _run_pipeline(model_client, jira_wb, pw_wb, fs_wb):
    """Run the QA pipeline. Email notifications are handled by webhook/server.py."""
    jira_id, cloud_id = _resolve_jira_ticket_and_cloud()
    pipeline_step = (os.environ.get("PIPELINE_STEP") or "full").strip().lower()
    is_split = pipeline_step in _SPLIT_PIPELINE_STEPS

    # Dirs must exist so filesystem MCP write_file succeeds (paths relative to AUTOMATION_PROJECT_ROOT / repo root).
    _project_root = _automation_project_root()
    _ensure_workspace_dirs(_project_root)

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
            2. Instruct AutomationAgent → generated_testscript/Script_{jira_id}.spec.ts → wait for "AutomationAgent Completed".
            3. Instruct ExecutionAgent → ResultReport → wait for "ExecutionAgent Completed".
            4. Instruct BugCreator → local bug drafts in ResultReport (human creates Jira issues) → wait for "BugCreator Completed".
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
            Your goal is to produce DETAILED, automation ready manual test cases that the AutomationAgent can 
            reliably convert into Playwright scripts. Generate detailed manual test cases never high level validation 
            statements only.
            
            WORKFLOW
            
            Step 1: Call the tool `searchJiraIssuesUsingJql` with a **minimal** payload so the response omits changelog, 
            transitions, watchers, attachments, worklog, subtasks, and issue links. 
            Use maxResults 1 and limit fields to test-design inputs only. Do **not** pass expand.
            
            {{
            "cloudId": "{cloud_id}",
            "jql": "key = {jira_id}",
            "maxResults": 1,
            "fields": {_jira_td_fields}
            }}
            
            If the tool schema expects `fields` as a JSON array of strings, 
            pass the same names as an array instead of one comma separated string.
            
            Step 2: Read the description, acceptance criteria, and latest comment in the User Story.
            Step 3: Generate all required test categories with full structure (see below). 
            Write the output file using the filesystem MCP `write_file` tool.
            
            ---
            REQUIRED TEST CASE STRUCTURE
            Every test case MUST contain these fields IN ORDER:
            - Test Case ID (format: <JIRA>-TC-NNN e.g. EC-298-TC-001)
            - Title
            - Test Type (exactly one of: Positive / Negative / Boundary / Edge- see REQUIRED TEST CATEGORIES RULE)
            - Preconditions  (once at the **top** of the test case only — see PRECONDITIONS RULE)
            - Steps          (each step = action + **expected result for that step** only — see PER-STEP FORMAT)
            - Expected Result (one **summary** at the end of the test case after all steps — must match the last step outcome)
            
            ---
            PRECONDITIONS RULE (MANDATORY)
            
            Put **Preconditions** once per test case (before Steps). Add Just one line,
            RULE — PRECONDITIONS MUST BE COMPLETE AND SELF-CONTAINED:
            Every test case must be independently executable from a clean 
            app state. Never assume state carries over from a previous test.
           STANDARD PRECONDITION (use this exact text for all non-login test cases):
           "User is already registered on the app"
            
            Examples:
            
            WRONG: ""User is logged into the application""
            CORRECT: "User is already registered on the app"
            
            The preconditions field must be specific enough that anyone reading it can set up the exact starting state without 
            referring to any other test case.
            
            Do NOT repeat preconditions under each step.
            
            ---
            PER-STEP FORMAT (MANDATORY)
            
            Under **Steps**, each step has **only**:
            1) The **action** (one UI action per step)
            2) The **expected result for that step**
            
            Use this pattern for every step:
            
            <n>:
            Action: <single UI action>
            Expected result: <observable outcome after this action>
            
          Test Case Generation Rules:
          1. EVERY test case MUST start with these exact steps before any other action:
             Step 1: Navigate to the application URL.
             Step 2: Enter username in the Username field.
             Step 3: Enter password in the Password field.
             Step 4: Click the Login button.
             Expected result for Step 4: User is redirected to the inventory page.
             EXCEPTION: Negative test cases that specifically test login failure
             (wrong credentials, empty fields) do not need to navigate after login.
             NO OTHER EXCEPTIONS. Even if the precondition says "user is logged in",
             still include the login steps.
            
            - Test Data inside each step.
            
            FORBIDDEN
            - Bare action lines with no **Expected result**
            - Only final Expected Result without per-step validations
            
            The **Expected Result** at the end is still required as a summary.
            
            ---
            STEP RULES (CRITICAL FOR AUTOMATION)
            
            Each **Action** MUST be a SINGLE UI action. Use only:
            
            - Navigate to URL
            - Enter text in field
            - Click button
            - Select dropdown value
            - Verify element visibility
            - Verify error message
            - Verify page navigation
            
            FORBIDDEN: vague actions like "Validate login works"
            
            ---
            EXAMPLE
            
            Test Data:
            username: standard_user
            password: secret_sauce
            
            Steps:
            
            Step 1:
            Action: Navigate to the application URL (use APP_URL from file header only).
            Expected result: Login page loads; username field, password field, and Login button are visible.
            
            Step 2:
            Action: Enter "standard_user" in the Username field.
            Expected result: Username field shows standard_user.
            
            Step 3:
            Action: Enter "secret_sauce" in the Password field.
            Expected result: Password field holds the value (masked).
            
            Step 4:
            Action: Click the Login button.
            Expected result: User is redirected to the inventory page.
            
            Expected Result: User successfully logs in.
            
            ---
            REQUIRED TEST CATEGORIES RULE
            
            You MUST generate:
            
            - Positive Test Cases
            - Negative Test Cases
            - Boundary Test Cases
            - Edge Test Cases
            
            ---
            APP_URL RULE (MANDATORY)
            
            APP_URL: <application_url>
            
            Rules:
            - Must be FIRST line
            - Must appear ONLY ONCE
            - No quotes
            - No extra text
            
            ---
            FILE NAMING AND HANDLING
            
            - File path MUST be: TestCases/<JIRA_TICKET_ID>_Testcase.txt
            - Always overwrite existing file
            - Use `write_file` tool
            
            ---
            TOOL USAGE RULES
            
            ONLY allowed tool: `write_file`
            
            DO NOT:
            - create directories
            - generate automation scripts
            - generate Playwright code
            
            ---
            FINAL RESPONSE
            
            When done, reply EXACTLY:
            
            TestDesigner Completed
            
            Never output STOP or Proceed
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

            Rule 1 — FILE HEADER: Always start with valid TypeScript in this exact order:
            import {{ test, expect }} from '@playwright/test';
            const APP_URL = 'https://...';

            Then immediately insert this exact block (before any test(...) ) so actions and whole tests exceed Playwright defaults (often 30s):
            test.beforeEach(async ({{ page }}) => {{
              test.setTimeout(120_000);
              page.setDefaultTimeout(90_000);
            }});

            Rule 2 — NO HELPERS OR CONSTANTS: Do NOT declare any const, function, or arrow function outside test() blocks
            **except** APP_URL and the mandatory test.beforeEach timeout block from Rule 1.
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
            await expect(page).toHaveURL(/.*inventory\\.html/);
            await expect(page.locator('.inventory_list')).toBeVisible();

            Rule 7 — EACH TEST must start with these two lines:
            await page.goto(APP_URL);
            await expect(page).toHaveURL(APP_URL);

           Rule 8 - PARENTHESES (CRITICAL): Every expect() call wraps a locator() call.
          The locator() closing ) must come BEFORE the . that calls the assertion method.
        
          CORRECT:  await expect(page.locator('selector')).toBeVisible();
          WRONG:    await expect(page.locator('selector').toBeVisible());
        
          After writing EVERY expect() line, count the closing parentheses:
          - page.locator('selector')  closes with )
          - expect(...)               closes with )
          - The assertion method      closes with ()
          Total closing parens on one line = 3.
          If you count only 2 closing parens - the parenthesis is wrong, fix it before moving on.
          The only fix needed is moving one ) from inside to outside:
            WRONG:   .locator('selector').toBeVisible());
            CORRECT: .locator('selector')).toBeVisible();
            

            Rule 10 — CLOSING BRACES: Every test() block must end with }});
            After writing all assertions for a test case, always close with:
                }});
            Before writing the next test() block, confirm the previous one is closed.
            The final line of the entire file must be }}); with no trailing code after it.
            
            FILE HANDLING
            Write the script to: generated_testscript/Script_{jira_id}.spec.ts
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
        - Read once: `generated_testscript/Script_{jira_id}.spec.ts` (filesystem `read_file`).
        - From it: `const APP_URL = '...'` (full https URL for `browser_navigate`), each `test('EXACT_TITLE', async ({{ page }}) => {{ ... }})` block = one logical test.
        - Use the **exact** string inside `test('...', ...)` as `title` in JSON. Do not use TestCases/*.txt for execution.

        ## Hard rules (pipeline + browser)
        1. **One session, one test at a time:** finish one `test()` (navigate → steps → pass/fail) before the next. No parallel browser tool calls; no second browser/window — that invalidates the run.
        2. **Per test, first browser_* call** is always `browser_navigate` with APP_URL (avoids about:blank). Then `browser_snapshot`. Do not snapshot before that navigate when starting a test.
        3. **Refs:** after each snapshot, use `ref` (and schema fields) from MCP for `browser_type`, `browser_click`, `browser_fill_form` — match the spec’s selectors (e.g. `[data-test="username"]`).
        4. **Artifacts before done:** use filesystem `write_file` to create **both** `ResultReport/execution_<jira_id>.json` and `ResultReport/result_<jira_id>.txt` **before** you say `ExecutionAgent Completed`.
        If the browser or a step fails, still write JSON with real `error_message` text (e.g. from tool errors or snapshot).

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

    # BugCreator: human-in-the-loop — local ResultReport/bug_*.txt only; Jira MCP not attached (workbench=[fs_wb]).
    # To re-enable agent-created Jira bugs: set workbench=[jira_wb, fs_wb] and restore in system_message —
    #   ORDER (1) jira_create_issue with cloudId from JIRA_CLOUD_ID / env, (2) write_file with first line JIRA_BUG_ID: <key from Jira>;
    #   FORBIDDEN: write_file before jira returns key; remove PENDING / USER_STORY lines if you return to Jira-first flow.
    BugCreator = AssistantAgent(
            name="BugCreator",
            model_client=model_client,
            workbench=[fs_wb],
            system_message=f"""
          You are a QA Bug Creation Agent. Your responsibility is to analyze Playwright execution reports and 
            produce **local bug draft files** for every failed test case. You do NOT execute tests. 
            You do NOT modify automation scripts. You ONLY read execution reports and write bug drafts to disk.
            You do **not** have Jira tools in this run — **never** attempt to create or update Jira issues.
            
            **SCOPE — THIS PIPELINE RUN ONLY (MANDATORY)**
            The User Story ticket for THIS run is: **{jira_id}**
            • You MUST ONLY read: ResultReport/execution_{jira_id}.json
            • You MUST NOT read, analyze, or create bugs from any other execution_*.json (other User Stories).
            • You MUST ONLY write local bug files matching: ResultReport/bug_{jira_id}_*.txt
            
            **PAY ATTENTION: VERY CRITICAL**
            Before any other action, confirm ResultReport/execution_{jira_id}.json exists.
                If that **exact** file does NOT exist:
                respond exactly with:
                REMAIN SILENT and WAIT for instructions
              

            INPUT SOURCE
            The ONLY execution report for this run is:
            ResultReport/execution_{jira_id}.json
            That JSON is the ONLY source of truth for pass/fail on this ticket.
            ----------------------------------------------------------------------------------------------------------------------------------------------
            
            PROCESS
            
            1. Read ONLY ResultReport/execution_{jira_id}.json (no other execution JSON files).
            2. Identify all failed test cases in that file.
            
            4.TESTCASE STEP EXTRACTION RULE
               When a failed test case is detected from execution_{jira_id}.json,
               use the failed test title to find the matching section inside the **single** manual test file for that ticket.
               Steps to follow:
            1. Use ResultReport/execution_{jira_id}.json (already read above).
            2. Identify the failed test title from failed_test_details.
                Example: "Positive Test Case: Successful Login".
            3. Read **exactly one** manual test file: TestCases/{jira_id}_Testcase.txt
               (same ticket as this run — do not open TestCases for any other id).
               Do NOT read TestCases/<JIRA>_TC-NNN.txt or any per-test-case filenames — those files do not exist.
               Use filesystem MCP read_file only on that path.
            4. Inside that file, search for the section whose title corresponds to the failed test title.
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
            IMPORTANT RULES: Steps must come from TestCases/{jira_id}_Testcase.txt only.
            Do not invent steps. If the matching section cannot be found in that file, fall back to generic reproduction steps.
            
            BUG DRAFT FILE RULE (NO JIRA — HUMAN CREATES ISSUES LATER)
            For each failed test in execution_{jira_id}.json you MUST write exactly one local draft using filesystem `write_file` only.
            A human validates ResultReport/bug_*.txt and files the real Bug in Jira when ready.

            ORDER (MANDATORY):
            For **each** failed test case (same order as `failed_test_details` in the JSON):
              (1) `write_file` exactly one path: ResultReport/bug_{jira_id}_<short sanitized failed-test title>.txt
              (2) **Line 1** — provisional id until a human files Jira (never use the User Story key **{jira_id}** here):
                  • One failed test total: `JIRA_BUG_ID: PENDING`
                  • Multiple failures: `JIRA_BUG_ID: PENDING-001`, `PENDING-002`, … (001 = first row in failed_test_details, then increment).
              (3) **Line 2**: `USER_STORY: {jira_id}`
              (4) **Rest of file**: suggested Jira fields and full description a human can copy-paste (Summary, Issue Type Bug,
                  Script Name, Test Case, Environment QA, Error Message from JSON, Expected/Actual, Steps to Reproduce from TestCases file, Automation Evidence).

            FORBIDDEN:
            • Any Jira MCP / Atlassian tool calls (not available in this mode).
            • Line 1 equal to **{jira_id}** or any existing Story/Epic key as the “bug id”.
            • bug_* filenames for any ticket other than **{jira_id}**.
            • Reading execution JSON for any ticket other than **{jira_id}**.

            Suggested Jira fields (include in the draft body for the human):
            Issue Type: Bug
            Summary: <JIRA_TICKET_ID>_<FAILED_TEST_TITLE>
            Description must contain:
            Script Name: <script name>
            Test Case: <failed test title>
            Environment: QA
            Execution Time: <timestamp if available>
            Error Message: <error message>
            Expected Result: expected result from the test case file.
            Actual Result: Application produced the error captured during test execution.

            Example draft body (after line 2) — same structure a human would paste into Jira:
                Script Name: Script_EC-298.spec.ts
                Test Case: Edge Test Case: Long Username and Password
                Environment: QA
                Error Message: Timeout waiting for expected error message to be visible.
                Expected Result: Application should behave as defined in the test case.
                Steps to Reproduce:
                1.)Navigate to the relevant application page.
                2.)Perform the scenario described in the failed test case.
                3.)Observe the failure described in the error message.
                4.)Automation Evidence: Failure detected during automated Playwright execution.

            ---

            LOCAL FILE CREATION (REQUIRED — QALead email / human review)
            Write via filesystem MCP `write_file` only. Location: ResultReport
            File name: bug_{jira_id}_<FAILED_TEST_TITLE>.txt
            Example: ResultReport/bug_EC-298_Login button validation failure.txt

            After a human creates the Bug in Jira, they should replace line 1 with: JIRA_BUG_ID: <real key e.g. EC-312>.

            -------------------------------------------------------------------
            
            CRITICAL RULES
            • Always rely ONLY on ResultReport/execution_{jira_id}.json for this run (no other stories)
            • Do NOT assume failures
            • Do NOT create local bug files if there are no failed tests in that JSON
            • Create **one** local bug_* file per failed test (`write_file` only)
            • Do NOT modify execution reports
            • Do NOT modify automation scripts
            ----------------------------------
            
            FINAL RESPONSE
            When processing execution_{jira_id}.json and local bug draft files for this ticket only is finished, respond EXACTLY with:
            BugCreator Completed
            Do NOT include any additional text.
            Never output STOP or Proceed - only QALead uses those.
        """
    )

    _qalead_critical = """
            ══════════════════════════════════════════════
            CRITICAL RULE - FULL / GENERIC PIPELINE
            ══════════════════════════════════════════════
            The initial user TASK tells you which agents to instruct and when to reply STOP.
            When the task says reply STOP - output exactly STOP with no other text (no name prefix, no colon, no period).
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
No agent name, no colon, no period, no markdown, no spaces, no explanation - only STOP.
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
                f"write generated_testscript/Script_{jira_id}.spec.ts. "
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
                f"(1) read_file generated_testscript/Script_{jira_id}.spec.ts "
                f"(2) run each test() via Playwright MCP; first browser_* per test: browser_navigate(APP_URL from script) "
                f"(3) write_file ResultReport/execution_{jira_id}.json "
                f"(4) write_file ResultReport/result_{jira_id}.txt (summary) "
                f"(5) when done, send one TextMessage whose content is exactly: ExecutionAgent Completed "
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
                f"Jira ticket {jira_id}. Analyze ONLY ResultReport/execution_{jira_id}.json; for each failure write "
                f"ResultReport/bug_{jira_id}_*.txt (local drafts only — no Jira). Follow system_message for line 1/2 format. "
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
    _maybe_write_execution_stub(_project_root, jira_id, pipeline_step)
    _print_token_usage_summary(model_client, pipeline_step, jira_id)


if __name__ == "__main__":
    asyncio.run(main())


import os
import asyncio
import json
import requests

from dotenv import load_dotenv

from autogen_agentchat.agents import AssistantAgent, UserProxyAgent
from autogen_agentchat.conditions import TextMessageTermination, MaxMessageTermination
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.ui import Console

from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_ext.tools.mcp import StdioServerParams, McpWorkbench

load_dotenv()


async def main():
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

    playwright_server = StdioServerParams(
      command="npx", args=["-y", "@playwright/mcp@latest"], read_timeout_seconds=120)

    filesystem_server = StdioServerParams(
        command="npx",
        args=[
            "-y",
            "@modelcontextprotocol/server-filesystem",
            "/Users/ishir/PycharmProjects/AgenticAIAutogen"
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


async def _run_pipeline(model_client, jira_wb, pw_wb, fs_wb):
    """Run the QA pipeline. Email notifications are handled by webhook/server.py."""
    jira_id = os.environ.get("JIRA_TICKET_ID", "EC-298")
    pipeline_step = (os.environ.get("PIPELINE_STEP") or "full").strip().lower()

    _project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    os.makedirs(os.path.join(_project_root, "TestCases"), exist_ok=True)
    os.makedirs(os.path.join(_project_root, "ResultReport"), exist_ok=True)

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
    elif pipeline_step == "testcases":
        _qalead_scope = """
            THIS RUN: only TestDesigner is in the chat. Instruct ONLY TestDesigner.
            NEVER mention AutomationAgent, ExecutionAgent, or BugCreator.
            """
    elif pipeline_step == "automation":
        _qalead_scope = """
            THIS RUN: only AutomationAgent is in the chat. Instruct ONLY AutomationAgent.
            NEVER mention TestDesigner, ExecutionAgent, or BugCreator.
            """
    elif pipeline_step == "execute":
        _qalead_scope = """
            THIS RUN: only ExecutionAgent is in the chat. Instruct ONLY ExecutionAgent.
            NEVER mention TestDesigner, AutomationAgent, or BugCreator.
            """
    elif pipeline_step == "bugs":
        _qalead_scope = """
            THIS RUN: only BugCreator is in the chat. Instruct ONLY BugCreator.
            NEVER mention TestDesigner, AutomationAgent, or ExecutionAgent.
            """
    else:
        _qalead_scope = f"""
            Unrecognized PIPELINE_STEP; follow the user task message only. (step={pipeline_step!r})
            """

    TestDesigner = AssistantAgent(
            name="TestDesigner",
            model_client=model_client,
            workbench=[jira_wb, fs_wb],
            system_message=f"""
You are a QA Test Case Design expert. Your goal is to produce DETAILED, automation-ready manual test cases that the AutomationAgent can reliably convert into Playwright scripts. 
Generate detailed manual test cases—never high-level validation statements only.

---
WORKFLOW

Step 1: Call the tool `searchJiraIssuesUsingJql` with:
{{
  "cloudId": "67d1724f-9c51-4717-bc9a-687b0f5aacd7",
  "jql": "key = {jira_id}"
}}

Step 2: Read ONLY the Jira issue description, acceptance criteria, and comments returned from Step 1.
IMPORTANT: Do NOT follow any Confluence page links. Do NOT call any Confluence tools. Use only the Jira issue data returned by searchJiraIssuesUsingJql.

Step 3: Generate all required test categories with full structure (see below). 
Write the output file using the filesystem MCP `write_file` tool.

---
REQUIRED TEST CASE STRUCTURE

Every test case MUST contain these fields:

- Test Case ID  (label MUST be exactly this spelling: `Test Case ID:` — do NOT use `TC-ID`, `TestCase ID`, or variants)
- Title
- Test Type (exactly one of: Positive / Negative / Boundary / Edge)
- Preconditions
- Test Data
- Steps
- Expected Result

Important: Test Case ID values must look like `{jira_id}-TC-001`, `{jira_id}-TC-002`, … (full Jira key prefix).

---
STEP RULES (CRITICAL FOR AUTOMATION)

You MUST write a "Steps:" section for every test case. Each step must be a numbered line (1. ..., 2. ..., etc.). 
    Never omit the Steps section or leave it empty.
- Steps MUST be numbered, clear, and executable (one concrete UI or system action per step).
- For EACH step, add an "Expected Result:" line immediately after the step, describing the expected outcome of that step only. Keep the overall "Expected Result:" at the end for the full test case.

Each step MUST represent a SINGLE UI action that can be automated. Use only actions such as:

- Navigate to URL
- Enter text in field
- Click button
- Select dropdown value
- Verify element visibility
- Verify error message
- Verify page navigation

FORBIDDEN: Vague statements like "Validate login works" or "Check that the form works."

REQUIRED: Explicit, one-action-per-step instructions. Example format:

Steps:
1. Navigate to the application URL
   Expected Result: <expected outcome for this step>
2. Enter "<exact username from Jira issue>" in the Username field
   Expected Result: Username field contains the entered value.
3. Enter "<exact password from Jira issue>" in the Password field
   Expected Result: Password field contains the entered value.
4. Click the Login button
   Expected Result: <expected outcome for this step>
5. Verify the user is redirected to the inventory page
   Expected Result: <expected outcome for this step>
Expected Result: <overall expected result for the test case>

CRITICAL: Every test case in the file MUST have a "Steps:" section with at least one numbered step (e.g. "1. ..."). 
Never write a test case without steps.

---
TEST DATA (MANDATORY — COPY EXACTLY FROM JIRA)

Every test case MUST include explicit test data inputs taken VERBATIM from the Jira issue description or acceptance criteria.

RULES:
- NEVER invent, guess, abbreviate, or truncate any test data value.
- Copy every credential, URL, username, password, and expected message character-for-character from the Jira issue.
- If no test data is provided in the Jira issue for a field, mark it as: <not specified in Jira>

Use this format:
Test Data:
username: <exact value from Jira issue>
password: <exact value from Jira issue>

(Adjust field names and values per test case; always list concrete values from Jira, never placeholders.)

---
REQUIRED TEST CATEGORIES

You MUST generate test cases in all four categories:

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

- You MUST call filesystem `write_file` once with the **exact** relative path: `TestCases/{jira_id}_Testcase.txt`
  (underscore before Testcase, capital T — this path is required for the pipeline and database sync).
- Write the **full** file content in that single call. Do not use a different folder or filename.
- If the file already exists, OVERWRITE it (no numbered variants).
- Before calling `write_file`, verify: first line is `APP_URL: ...`; sections `Positive Test Cases:`, `Negative Test Cases:`, `Boundary Test Cases:`, `Edge Test Cases:` each contain **at least one** test case block with `Test Case ID:` and `Steps:`.
- Do **not** reply `TestDesigner Completed` until `write_file` has succeeded for `TestCases/{jira_id}_Testcase.txt`.

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

Only after `write_file` to `TestCases/{jira_id}_Testcase.txt` succeeds, reply EXACTLY with (no other text):

TestDesigner Completed
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
Copy ALL values (usernames, passwords, URLs) character-for-character from the test case file.
NEVER abbreviate or guess any value. If the file says password: secret_sauce, use 'secret_sauce' exactly.

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

Rule 5 — ERROR MESSAGES: Use toContainText() with a partial string from the expected error.
  await expect(page.locator('[data-test="error"]')).toBeVisible();
  await expect(page.locator('[data-test="error"]')).toContainText('Username and password do not match');
  Never use toHaveText() for error messages.

Rule 6 — POSITIVE TESTS (successful login): After clicking login, always verify the redirect URL and page content:
  await expect(page).toHaveURL(/.*inventory\\.html/);
  await expect(page.locator('.inventory_list')).toBeVisible();

Rule 7 — EACH TEST must start with these two lines:
  await page.goto(APP_URL);
  await expect(page).toHaveURL(APP_URL);

Rule 8 — PARENTHESES: Every expect() call wraps a locator() call. Always close both parentheses separately:
  await expect(page.locator('selector')).toHaveValue('value');
  Note: page.locator('selector') has its own closing ) and then expect(...) has its own closing ).

FILE HANDLING
Write the script to: generated_testscript/Script_{jira_id}_.spec.ts
If the file already exists, OVERWRITE it. Do NOT create numbered variants.
Use the filesystem MCP write_file tool.
Do NOT execute the scripts.

ALWAYS end your response with exactly: AutomationAgent Completed
"""
    )

    ExecutionAgent = AssistantAgent(
            name="ExecutionAgent",
            model_client=model_client,
            workbench=[pw_wb, fs_wb],
            system_message="""
        You are a QA Automation Script Execution Agent. 
        Your responsibility is to execute Playwright automation tests using Playwright MCP browser tools and generate execution artifacts that will be consumed by other AI agents. You DO NOT generate new automation scripts. You DO NOT modify test scripts. You EXECUTE the existing test flow using Playwright MCP browser tools.

            EXECUTION APPROACH
            Use Playwright MCP browser tools such as:
            browser_navigate
            browser_click
            browser_type
            browser_wait_for
            browser_screenshot
            Execute the user flow step-by-step and validate expected outcomes.
            
            EXECUTION WORKFLOW
            
            1. Locate Automation Scripts
               Look inside the folder: generated_testscript
               Find all files with extension: .spec.ts
               Example: generated_testscript/Script_EC-298.spec.ts
               Use filesystem MCP tools to read the file.
            
            2. Extract Jira Ticket ID
               Example file name: Script_EC-298.spec.ts
               Extract: JIRA_TICKET_ID = EC-298
            
            3. Understand the Test Flow
               Read the test script and identify:
               • target URL
               • test steps
               • expected validations
            
            4. Execute Test Using MCP Browser Tools
               Execute the steps sequentially using Playwright MCP tools.
               Example execution pattern:
               browser_navigate(url)
               browser_type(username)
               browser_type(password)
               browser_click(login_button)
               browser_wait_for(expected_element)
               Validate outcomes using page snapshot or expected element visibility.
            
            5. Track Execution Results
               For each test case track:
               • test name
               • PASS or FAIL
               • error message if failed
            
            6. Generate Execution Artifacts
               Save all files in folder: ResultReport
               Create the folder if it does not exist.
               Use filesystem MCP write_file tool to write or overwrite files.
               
               POST ACTION VALIDATION RULE

                After performing a critical action such as clicking Login, navigation, or submitting a form, the agent must wait for the expected next state.
                
                Use browser_wait_for with a reasonable timeout to detect the next expected page state.
                
                If the expected page state appears within the timeout → mark the step as PASSED.
                
                If the expected page state does NOT appear within the timeout → treat this as a FAILED test step.
                
                When a failure occurs:
                
                Immediately capture screenshot using browser_screenshot.
                
                Record the error message and failed step.
                
                Mark the test case as FAILED.
                
                Continue execution of remaining test cases.
                
                Never assume success.
                Always treat timeout or missing expected state as a failure condition.
            
            FILE 1 — EXECUTION JSON (SOURCE OF TRUTH)
            File name format: execution_<JIRA_TICKET_ID>.json
            Example: ResultReport/execution_EC-298.json
            Structure example:
            {
            "jira_ticket_id": "EC-298",
            "total_tests": 4,
            "passed_tests": 3,
            "failed_tests": 1,
            "failed_test_details": [
            {
            "title": "Test Name",
            "error_message": "Failure description"
            }
            ]
            }
            Do NOT generate execution_<JIRA_ID>.json until ALL test cases have completed execution.

            The JSON execution report must be written ONLY after:
            1. All test cases have been executed
            2. All pass/fail results have been collected
            3. All screenshots for failures are captured
                        
            FILE 2 — TEXT EXECUTION SUMMARY
            File name format: result_<JIRA_TICKET_ID>.txt
            Example: ResultReport/result_EC-298.txt
            Content must include:
            Jira Ticket ID
            Total Tests
            Passed Tests
            Failed Tests
            Failed Test Titles
            Error Messages
            
            FILE 3 — FAILURE SCREENSHOT
            If a test fails capture screenshot using browser_screenshot
            File name format: screenshot_<JIRA_TICKET_ID>.png
            Example: ResultReport/screenshot_EC-298.png
            
            CRITICAL RULES
            • Always execute test flow using MCP browser tools
            • Do NOT generate new automation scripts
            • Do NOT modify the test script
            • Do NOT skip test cases
            • Always generate execution artifacts
            • JSON execution report is the source of truth
            
            FINAL RESPONSE
            When execution is finished respond EXACTLY with:
            ExecutionAgent Completed
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
            Positive Test Cases:
            5. Test Case: Successful Login
            * Precondition: User is on the login page.
            * Steps:
            1. Navigate to https://www.saucedemo.com
            2. Enter standard_user in Username field
            3. Enter sauce in Password field
            4. Click Login
            * Expected Result: User is redirected to /inventory.html page and product list is visible.
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
            Expected Result: Application should behave as defined in the test case.
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
        """
    )

    QALead = AssistantAgent(
            name="QALead",
            model_client=model_client,
            workbench=[],
            system_message=f"""
            You are the QA Lead supervising the testing automation workflow.

            ══════════════════════════════════════════════
            CRITICAL RULE — READ THIS FIRST
            ══════════════════════════════════════════════
            The initial user TASK message tells you EXACTLY which agent to instruct
            and EXACTLY when to reply STOP. Follow that task and the scope below.
            When the task says reply STOP — do so immediately with no other text.
            Do NOT send emails. Email notifications are handled externally.
            ══════════════════════════════════════════════

            SCOPE FOR THIS RUN (PIPELINE_STEP={pipeline_step}):
            {_qalead_scope}

            Each agent must only perform their own responsibility.
            Agents must NOT perform each other's responsibilities.

            """
    )

    if pipeline_step == "testcases":
        task = (
            f"Instruct TestDesigner to generate test cases for Jira ticket {jira_id}. "
            f"When TestDesigner replies 'TestDesigner Completed', you MUST reply with exactly: STOP"
        )
        termination = TextMessageTermination("STOP") | MaxMessageTermination(30)
        participants = [QALead, TestDesigner]

    elif pipeline_step == "automation":
        task = (
            f"Instruct AutomationAgent to generate Playwright scripts for Jira ticket {jira_id}. "
            f"When AutomationAgent replies 'AutomationAgent Completed', you MUST reply with exactly: STOP"
        )
        termination = TextMessageTermination("STOP") | MaxMessageTermination(30)
        participants = [QALead, AutomationAgent]

    elif pipeline_step == "execute":
        task = (
            f"Instruct ExecutionAgent to execute the Playwright scripts for Jira ticket {jira_id} "
            f"and save results to ResultReport. "
            f"When ExecutionAgent replies 'ExecutionAgent Completed', you MUST reply with exactly: STOP"
        )
        termination = TextMessageTermination("STOP") | MaxMessageTermination(30)
        participants = [QALead, ExecutionAgent]

    elif pipeline_step == "bugs":
        task = (
            f"Instruct BugCreator to analyze execution results in ResultReport and create Jira bugs "
            f"for any failed tests for Jira ticket {jira_id}. "
            f"When BugCreator replies 'BugCreator Completed', you MUST reply with exactly: STOP"
        )
        termination = TextMessageTermination("STOP") | MaxMessageTermination(30)
        participants = [QALead, BugCreator]

    else:
        task = (
            f"Start QA automation pipeline for Jira ticket {jira_id} by instructing all agents in order: "
            f"TestDesigner → AutomationAgent → ExecutionAgent → BugCreator. "
            f"After all agents have finished, reply exactly: STOP"
        )
        termination = TextMessageTermination("STOP") | MaxMessageTermination(60)
        participants = [QALead, TestDesigner, AutomationAgent, ExecutionAgent, BugCreator]

    team = RoundRobinGroupChat(
        participants=participants,
        termination_condition=termination
    )

    await Console(
        team.run_stream(task=task)
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

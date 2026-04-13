import os
import asyncio
import json

from dotenv import load_dotenv

from autogen_agentchat.agents import AssistantAgent, UserProxyAgent
from autogen_agentchat.conditions import TextMessageTermination, MaxMessageTermination
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.ui import Console

from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_ext.tools.mcp import StdioServerParams, McpWorkbench

load_dotenv()

_REPO_ROOT = os.path.abspath(os.path.dirname(__file__))


def _ensure_workspace_dirs() -> None:
    """Create each pipeline folder only if it does not already exist."""
    for name in ("TestCases", "generated_testscript", "ResultReport"):
        path = os.path.join(_REPO_ROOT, name)
        if not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)


async def main():
    jira_id = os.environ.get("JIRA_TICKET_ID", "EC-298")
    cloud_id = (os.environ.get("JIRA_CLOUD_ID") or "").strip()
    if not cloud_id:
        raise ValueError("Set JIRA_CLOUD_ID in .env (Atlassian cloud UUID for MCP Jira tools).")
    _ensure_workspace_dirs()
    model_client = OpenAIChatCompletionClient(
        model="gpt-4o-mini",
        api_key=os.environ["OPENAI_API_KEY"]
    )

    os.environ["ATLASSIAN_EMAIL"] = os.getenv("JIRA_USERNAME")
    os.environ["ATLASSIAN_API_TOKEN"] = os.getenv("JIRA_API_TOKEN")

    atlassian_server = StdioServerParams(
        command="npx",
        args=[
            "-y",
            "mcp-remote",
            "https://mcp.atlassian.com/v1/sse"
        ],
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



    async with  McpWorkbench(atlassian_server) as jira_wb,McpWorkbench(playwright_server) as pw_wb,McpWorkbench(filesystem_server) as fs_wb :

        print("✅ MCP Servers Connected")

        ExecutionAgent = AssistantAgent(
            name="ExecutionAgent",
            model_client=model_client,
            workbench=[pw_wb, fs_wb],
            system_message="""
             You are a QA Automation Script Execution Agent. Your responsibility is to execute Playwright automation tests using Playwright MCP browser tools and generate execution artifacts that will be consumed by other AI agents. You DO NOT generate new automation scripts. You DO NOT modify test scripts. You EXECUTE the existing test flow using Playwright MCP browser tools.

            EXECUTION APPROACH
            Use Playwright MCP browser tools such as:
            browser_navigate
            browser_click
            browser_type
            browser_wait_for
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
            system_message=f"""
            ## You are a QA Bug Creation Agent. Your responsibility is to analyze Playwright execution reports and 
            create bug records for every failed test case. You do NOT execute tests. 
            You do NOT modify automation scripts. You ONLY read execution reports and create bugs.
            
            **SCOPE — THIS PIPELINE RUN ONLY (MANDATORY)**
            The User Story ticket for THIS run is: **{jira_id}**
            • You MUST ONLY read: ResultReport/execution_{jira_id}.json
            • You MUST NOT read, analyze, or create bugs from any other execution_*.json.
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
            
            1. Read ONLY ResultReport/execution_{jira_id}.json (no other execution JSON).
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
            Error Message:<error_message from execution JSON>,
            Automation Evidence:Failure detected during automated Playwright execution.
            IMPORTANT RULES: Steps must come from TestCases/{jira_id}_Testcase.txt only.
            Do not invent steps. If the matching section cannot be found in that file, fall back to generic reproduction steps.

            BUG CREATION RULE
            For each failed test in execution_{jira_id}.json you MUST create a real Bug in Jira, then write the local mirror file.
            
            ORDER (MANDATORY — NEVER REVERSE OR BATCH-SKIP JIRA):
            For **each** failed test case:
              (1) Call the Jira MCP tool to **create** the Bug (e.g. jira_create_issue) and wait until the tool returns the **new issue key**
                  (a different key from the User Story — e.g. EC-412, not **{jira_id}**).
              (2) ONLY AFTER (1) succeeds, use filesystem `write_file` for exactly one file:
                  ResultReport/bug_{jira_id}_<short sanitized failed-test title>.txt
                  The **first line** MUST be: JIRA_BUG_ID: <the key returned in (1)>
            
            FORBIDDEN:
            • write_file for bug_{jira_id}_*.txt **before** jira_create_issue (or equivalent) returns the new Bug key for that failure.
            • Using **{jira_id}** (this User Story key) as JIRA_BUG_ID — only the **newly created Bug** key is allowed.
            • Creating bug files for any ticket other than **{jira_id}** in this run.
            
            Bug fields must be generated as follows:
            Issue Type: Bug
            Summary: <JIRA_TICKET_ID>_<FAILED_TEST_TITLE>
            Description must contain:
            Script Name: <script name>
            Test Case: <failed test title>
            Environment: QA
            Execution Time: <timestamp if available>
            Error Message: <error message>
            Expected Result: Application should behave as defined in the test case.
            Actual Result: Application produced the error captured during test execution.
            Steps to Reproduce:
            
            1. Navigate to the relevant application page.
            2. Perform the scenario described in the failed test case.
            3. Observe the failure described in the error message.
               Automation Evidence: Failure detected during automated Playwright execution.
            
            ---
            
            JIRA ISSUE CREATION
            Use the Jira MCP tool to create the bug in Jira Cloud.
            Use the provided cloudId when calling the Jira tool.
            Example Jira MCP tool call:
            jira_create_issue({{
            cloudId: "{cloud_id}",
            projectKey: "EC",
            issueType: "Bug",
            summary: "<JIRA_TICKET_ID>_<FAILED_TEST_TITLE>",
            description: `
            Script Name: <script name>
            
            Test Case:
            <FAILED_TEST_TITLE>
            
            Environment:
            QA
            
            Error Message: <error message>
            
            Steps to Reproduce:
            
            1. Navigate to the relevant application page
            2. Perform the scenario described in the test case
            3. Observe the failure described in the error message
            
            Automation Evidence:
            Failure detected during Playwright automated execution
            `
            }})
            --
            
            LOCAL FILE CREATION (FOR TESTING PURPOSE)
            After each successful Jira Bug create for this ticket, write one local file via filesystem MCP write_file.
            File location: ResultReport
            File name format:
            bug_{jira_id}_<FAILED_TEST_TITLE>.txt
            Example:
            ResultReport/bug_EC-298_Login button validation failure.txt
            The first line MUST be JIRA_BUG_ID: <new Bug key from Jira>, never **{jira_id}**.
            The remainder must include the bug summary and full bug description.
            -------------------------------------------------------------------
            
            CRITICAL RULES
            • Always rely ONLY on ResultReport/execution_{jira_id}.json for this run
            • Do NOT assume failures
            • Do NOT create Jira issues or local bug files if there are no failed tests in that JSON
            • Create one Jira Bug + one local bug_ file per failed test (Jira first, then write_file)
            • Do NOT modify execution reports
            • Do NOT modify automation scripts
            ----------------------------------
            
            FINAL RESPONSE
            When processing execution_{jira_id}.json (and Jira + local files for this ticket only) is finished respond EXACTLY with:
            BugCreator Completed
            Do NOT include any additional text.

"""
)
        QALead = AssistantAgent(
            name="QALead",
            model_client=model_client,
            system_message="""
                You are QALead, the Supervisor Agent responsible for orchestrating the QA automation workflow. 
                You manage two agents: ExecutionAgent and BugCreator. Your responsibility is to ensure correct 
                execution order and prevent BugCreator from running before execution artifacts are available.

                WORKFLOW OVERVIEW
                The workflow consists of two phases:
                
                Test Execution Phase (handled by ExecutionAgent). Tell ExecutionAgent to start execution and
                make sure that it executes successfully. Then save all the files successfully.
                
               
               Bug Creation Phase (handled by BugCreator)
                 ***Important***  Make sure BugCreator must remain silent and WAIT FOR ITS TURN
                 TILL ExecutionAgent completes . Once you make sure that .json file is added then ask BugCreatoe to start working.
                 If still, BugCreator disturbs, tell him to keep quiet.
                
                
                Critical:
                After instructing the agents to start working,
            remain silent until agents explicitly responds with their termination conditions
            example ExecutionAgent responds "ExecutionAgent Completed".
            Do not ask for status updates during execution.
                
                FINAL RESPONSE
                Only say STOP after:
                1. ExecutionAgent confirms "ExecutionAgent Completed"
                2. BugCreator confirms "BugCreator Completed"
                Do not include any additional text.
        """
        )
        print("executionAgent completed")
        team = RoundRobinGroupChat(
            participants=[QALead,ExecutionAgent,BugCreator ],
            termination_condition=TextMessageTermination("STOP") | MaxMessageTermination(100)
        )

        await Console(
            team.run_stream(
                task="Start executing the scripts "
            )
        )
    print("Agent finished execution")

if __name__ == "__main__":
    asyncio.run(main())

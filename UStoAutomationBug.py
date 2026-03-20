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

    async with McpWorkbench(atlassian_server) as jira_wb, McpWorkbench(playwright_server) as pw_wb,McpWorkbench(filesystem_server) as fs_wb :

        print("✅ MCP Servers Connected")

        TestDesigner = AssistantAgent(
            name="TestDesigner",
            model_client=model_client,
            workbench=[jira_wb, fs_wb],
            system_message="""
You are a QA Test Case Design expert. Your goal is to produce DETAILED, automation‑ready manual test cases that the AutomationAgent can reliably convert into Playwright scripts.

You MUST first normalize the incoming Jira User Story into a fixed internal structure, and then generate, review, and clean test cases based ONLY on that normalized view.

======================================================================
1) WORKFLOW (DO NOT SKIP ANY STEP)
======================================================================

Step 1 — Fetch the User Story
- Use the Jira MCP tools (for example `searchJiraIssuesUsingJql`) to fetch the issue for the current <JIRA_TICKET_ID>.
- Use a JQL like:
  key = <JIRA_TICKET_ID>
- Read:
  - Summary / title
  - Description
  - Acceptance criteria
  - All comments on the issue

- While processing comments:
  - Prefer comments from Product Owners, BAs, QAs, or stakeholders that clarify flows, rules, or edge cases.
  - Ignore chit‑chat, implementation notes, or content unrelated to behaviour (for example “I’ll pick this up tomorrow”).
  - Only incorporate concrete, requirement‑like statements into the normalized story (for example “Also support guest checkout”).

Step 2 — USER STORY NORMALIZATION (INTERNAL ONLY)
- Convert ALL the gathered information into the following EXACT normalized structure.
- This normalized structure is for your internal reasoning ONLY; DO NOT write it to any file.
- If a field is missing in Jira, leave it blank instead of guessing.

Normalized User Story:

Feature Name:
User Role:
Goal:
Preconditions:
Main Flow Steps:
Alternate / Exception Flows:
Business Rules:
Validations:
Post Conditions:

You MUST reason using this normalized structure before designing test cases.

Step 3 — TEST CASE GENERATION (STRUCTURED FORMAT)
- Based on the normalized story, generate manual test cases strictly in this structure:

Test Case ID
Title
Description
Preconditions
Test Steps (each step MUST be followed by an Expected Result for that step)
Expected Result (overall for the test case)
Test Data
Priority

Rules:
- You MUST write a "Steps:" section for every test case. Each step must be a numbered line (1. ..., 2. ..., etc.). Never omit the Steps section or leave it empty.
- Steps MUST be numbered, clear, and executable (one concrete UI or system action per step).
- For EACH step, add an "Expected Result:" line immediately after the step, describing the expected outcome of that step only. Keep the overall "Expected Result:" at the end for the full test case.
- Test Data must list explicit values or clearly state “None” if not required.
- Priority should reflect relative importance (e.g. High / Medium / Low) but do NOT invent business rules to justify it.
- Do NOT hallucinate any feature or rule that is not present in the normalized story.
- If some information is missing in the story, leave that field blank or clearly mark as “Not specified” instead of guessing.

Step 4 — TEST CASE REVIEW AND DEDUPLICATION
- Before writing the file, perform an explicit review pass:
  - Remove duplicate or very similar test cases.
  - Ensure every remaining test case is traceable to exactly one of:
    - Main Flow
    - Alternate / Exception Flow
    - Validation
    - Business Rule
  - Make sure there are no vague, generic, or purely “happy path only” test cases.
  - Ensure all steps are actionable and automation‑friendly (no “verify everything works” type steps).

Step 5 — CONSTRAINTS AND SAFETY
- You MUST NOT:
  - Hallucinate features, fields, or flows that do not exist in the story.
  - Assume implicit validations or business rules not clearly stated.
  - Introduce biased or overly specific test data such as real emails (e.g. gmail.com) or personal data.
  - Create vague or generic test cases (they must be specific to the normalized story).
- If a detail is unknown or missing, leave it blank or “Not specified”.

======================================================================
2) FILE FORMAT & APP_URL RULE
======================================================================

The output file MUST contain ONLY the cleaned, reviewed test cases in the structure above.

At the very top of the file, you MUST include the application URL in EXACTLY this format:

APP_URL: <application_url>

Example: APP_URL: https://example-app-url.com/

Rules:
- APP_URL must be the FIRST non‑empty line in the file.
- APP_URL must appear ONLY ONCE in the entire document.
- Do NOT wrap the URL in quotes.
- Do NOT add any text or explanation before or after the APP_URL line.
- Do NOT repeat the URL anywhere else in the document.

After the APP_URL line, list all final test cases in the required format. EVERY test case MUST include a "Steps:" section with numbered steps — do NOT omit steps.

Required block (copy this structure for each test case):

Test Case ID: ...
Title: ...
Description: ...
Preconditions: ...
Test Data:
<field>: <value>
Steps:
1. <step action>
   Expected Result: <expected outcome for this step>
2. <step action>
   Expected Result: <expected outcome for this step>
...
Expected Result: <overall expected result for the test case>
Priority: ...

CRITICAL: Every test case in the file MUST have a "Steps:" section with at least one numbered step (e.g. "1. ..."). Never write a test case without steps. Repeat this full block for every test case.

======================================================================
3) FILE NAMING, LOCATION, AND TOOL USAGE
======================================================================

- File path MUST be: TestCases/<JIRA_TICKET_ID>_Testcase.txt
  Example: TestCases/EC-298_Testcase.txt
- Always write files under the `TestCases` folder at the workspace root.
- If the file already exists, you MUST OVERWRITE it (do NOT create numbered variants).
- Before finishing, verify the file contains a "Steps:" section with numbered steps (1. ..., 2. ..., etc.) for every test case. If any test case is missing steps, add them and write the file again.
- You are ONLY allowed to use the filesystem MCP tool `write_file`.
- You must NOT:
  - create directories
  - generate automation scripts
  - generate JavaScript or Playwright code

Your responsibility is ONLY to generate high‑quality, normalized, reviewed manual test cases.
The only allowed output file location is:

TestCases/<JIRA_TICKET_ID>_Testcase.txt

======================================================================
4) FINAL RESPONSE
======================================================================

After successfully writing the final, cleaned test case file, reply EXACTLY with (no other text):

TestDesigner Completed
"""
            )



        AutomationAgent = AssistantAgent(
            name="AutomationAgent",
            model_client=model_client,
            workbench=[pw_wb, fs_wb],
            system_message="""
You are a Playwright automation expert who writes detailed scripts.

Goal: Read the manual test cases from TestCases/<JIRA_TICKET_ID>_Testcase.txt and convert EVERY test case into executable Playwright code.

CRITICAL — ONE TEST BLOCK PER TEST CASE:
- The Testcase file contains multiple test cases (Positive, Negative, Boundary, Edge).
- You MUST create ONE separate test() block for EACH test case listed in the file.
- Example: If the file has 4 test cases (1 Positive, 1 Negative, 1 Boundary, 1 Edge), the script MUST contain exactly 4 test() blocks.
- Each test() must have a clear, descriptive title that matches the scenario (e.g. "Positive: Successful login with valid credentials", "Negative: Invalid username shows error").
- Do NOT merge multiple test cases into a single test() block. ExecutionAgent and the execution report count each test() as one test; merging causes undercounting (e.g. 4 test cases showing as 1 or 2).

Playwright structure required:
  import { test, expect } from '@playwright/test';

  test('Positive: <title from test case>', async ({ page }) => { ... });
  test('Negative: <title from test case>', async ({ page }) => { ... });
  test('Boundary: <title from test case>', async ({ page }) => { ... });
  test('Edge: <title from test case>', async ({ page }) => { ... });

FILE HANDLING RULES:
- Read the test case file first using the filesystem MCP tool to get every scenario.
- Write a single Playwright script file under `generated_testscript` at the workspace root.
- File name exactly: Script_<JIRA_TICKET_ID>_.spec.ts
- If the file already exists, OVERWRITE it. Do NOT create numbered files.
- Use the filesystem MCP write_file tool.

IMPORTANT: Do not execute the scripts.

ALWAYS end with: AutomationAgent Completed
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

        print("executionAgent completed")

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
            Error Message:<error_message from execution JSON>,
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
            Steps to Reproduce:
            
            1. Navigate to the relevant application page.
            2. Perform the scenario described in the failed test case.
            3. Observe the failure described in the error message.
               Automation Evidence: Failure detected during automated Playwright execution.
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
            
            LOCAL FILE CREATION (FOR TESTING PURPOSE)
            For testing purposes also create a local file using filesystem MCP tool write_file.
            File location: ResultReport
            File name format:
            bug_<JIRA_TICKET_ID>_<FAILED_TEST_TITLE>.txt
            Example:
            ResultReport/bug_EC-298_Login button validation failure.txt
            File content must include the bug summary and full bug description.
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
            system_message="""
            You are the QA Lead supervising the testing automation workflow.
            
            Your responsibilities:
            
            1. Start the workflow for the given Jira story.
            2. Ask TestDesigner to generate all possible Positive, Negative, Boundary, and Edge test cases.
            3. Ensure test cases are saved in the TestCases folder with the file name <JIRA_TICKET_ID>_Testcase.txt.
            4. If the files are not created, ask TestDesigner to retry.
            
            5. Once test cases exist, instruct AutomationAgent to generate Playwright scripts save under folder generated_testscript.
            If scripts are missing, ask AutomationAgent to retry.
            
            7. Once Scripts are ready, Ask ExecutionAgent to execute the scripts and 
            save the results under ResultReport folder. If any testcase is failed , save the screenshots .
            
            8. Once Results are saved, ask BugCreator to analyse it and find out if any test is failed.
            Create a jira ticket for the failed test case .
            
            8. You have to make sure that
                TestDesigner must ONLY generate manual testcases.
                AutomationAgent must ONLY generate Playwright scripts.
                ExecutionAgent Must Only Execute the Playwright scripts.
                BugCreator must only create a jira ticket for the failed test case .
                
                Agents must NOT perform the other agent's responsibilities.
            8. Once all the subagents completed their tasks,  end the workflow.
            9 You also need to tell all your subagents to STOP interacting once they complete their tasks.
            
            When everything is completed respond exactly:
            
            STOP
            """
)

        # Step mode: testcases | automation | execute | bugs | full (or unset = full)
        pipeline_step = (os.environ.get("PIPELINE_STEP") or "full").strip().lower()
        jira_id = os.environ.get("JIRA_TICKET_ID", "EC-298")

        if pipeline_step == "testcases":
            task = f"Only instruct TestDesigner to generate test cases for Jira ticket {jira_id}. Do NOT ask AutomationAgent, ExecutionAgent, or BugCreator to do anything. When TestDesigner has finished and said 'TestDesigner Completed', reply exactly: STOP"
            termination = TextMessageTermination("STOP") | MaxMessageTermination(15)
        elif pipeline_step == "automation":
            task = f"Only instruct AutomationAgent to generate Playwright scripts from the existing test case file for Jira ticket {jira_id}. Do NOT run TestDesigner, ExecutionAgent, or BugCreator. When AutomationAgent has finished and said 'AutomationAgent Completed', reply exactly: STOP"
            termination = TextMessageTermination("STOP") | MaxMessageTermination(15)
        elif pipeline_step == "execute":
            task = f"Only instruct ExecutionAgent to execute the Playwright scripts for Jira ticket {jira_id} and save results to ResultReport. Do NOT run TestDesigner, AutomationAgent, or BugCreator. When ExecutionAgent has finished and said 'ExecutionAgent Completed', reply exactly: STOP"
            termination = TextMessageTermination("STOP") | MaxMessageTermination(30)
        elif pipeline_step == "bugs":
            task = f"Only instruct BugCreator to analyze execution results in ResultReport and create Jira bugs for any failed tests (Jira ticket {jira_id}). Do NOT run TestDesigner, AutomationAgent, or ExecutionAgent. When BugCreator has finished and said 'BugCreator Completed', reply exactly: STOP"
            termination = TextMessageTermination("STOP") | MaxMessageTermination(15)
        else:
            task = f"Start QA automation pipeline for Jira ticket {jira_id} by instructing all the agents "
            termination = TextMessageTermination("STOP") | MaxMessageTermination(60)

        team = RoundRobinGroupChat(
            participants=[QALead, TestDesigner, AutomationAgent, ExecutionAgent, BugCreator],
            termination_condition=termination
        )

        await Console(
            team.run_stream(task=task)
        )
    print("Agent finished execution")




if __name__ == "__main__":
    asyncio.run(main())
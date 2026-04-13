import os
import asyncio
from autogen_agentchat.agents import AssistantAgent, UserProxyAgent
from autogen_agentchat.conditions import TextMessageTermination, MaxMessageTermination
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.ui import Console
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_ext.tools.mcp import StdioServerParams, McpWorkbench

from dotenv import load_dotenv

load_dotenv()

_REPO_ROOT = os.path.abspath(os.path.dirname(__file__))


def _ensure_workspace_dirs() -> None:
    """Create each pipeline folder only if it does not already exist."""
    for name in ("TestCases", "generated_testscript", "ResultReport"):
        path = os.path.join(_REPO_ROOT, name)
        if not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
JIRA_URL = os.getenv("JIRA_URL")
JIRA_USERNAME = os.getenv("JIRA_USERNAME")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
JIRA_PROJECTS_FILTER = os.getenv("JIRA_PROJECTS_FILTER")


async def main():
    _ensure_workspace_dirs()
    model_client = OpenAIChatCompletionClient(model="gpt-4o", api_key=os.environ["OPENAI_API_KEY"])

    atlassian_server = StdioServerParams(
        command="npx",
        args=["-y", "mcp-remote", "https://mcp.atlassian.com/v1/sse"],
        read_timeout_seconds=120,
    )
    atlassian_workbench = McpWorkbench(atlassian_server)

    file_system_server = StdioServerParams(command="npx", args=[
        "-y",
        "@modelcontextprotocol/server-filesystem",
        "/Users/ishir/PycharmProjects/AgenticAIAutogen"
    ], read_timeout_seconds=60)
    fs_workbench = McpWorkbench(file_system_server)

    playwright_server = StdioServerParams(
        command="npx", args=["-y", "@playwright/mcp@latest"], read_timeout_seconds=120)
    playwright_workbench = McpWorkbench(playwright_server)


    async with atlassian_workbench as jira_wb, fs_workbench as fs_wb , playwright_workbench as pw_wb:
        jira_agent = AssistantAgent(
        name="jira_agent",
        model_client=model_client,
        workbench=[jira_wb, fs_wb],  # <- LIST, not comma-separated args
        system_message= "You are a Testcase writing expert and can write detailed TestCases "
                        "based on requirement and user stories analysis .\n"
                        "Goal: Analyze User stories and create detailed positive, negative and edge test cases"
                        " for the Bank Project (project key: BP).\n"
                         "Tasks:\n"
                         "1.Use the `searchJiraIssuesUsingJql` tool with EXACTLY these parameters:"
                        " {\"cloudId\": \"be529790-31d6-4620-8d0e-3e54d5bb1e48\", \"jql\": \"project = BP AND issuetype = Story AND sprint in openSprints() "
                        "AND status != DONE ORDER BY created DESC` "
                        "If you do not find the required URL, try again after waiting for 2 seconds"
                        "2.Carefully read all retrieved User story descriptions with acceptance criteria,"
                        "think of all positive, negative and edge test scenario  ."
                        "design a detailed test scenario document.\n"
                        "Be specific in your test design:\n"
                        "- Provide clear, step-by-step manual testing instructions.\n"
                        "- State the expected outcomes or validations for each step.\n"
                        "When your analysis is complete:\n"
                        "- Output the final testing steps and write them to a new file under a folder TestCases, "
                        "Use the write_file tool from the filesystem server "
                        "The file name MUST follow this exact format:" 
                       " TestCase_<JIRA_TICKET_ID>.txt"
                        " when everything is done , reply with exactly:Task Completed\n"
        )

        script_creator = AssistantAgent(
            name="script_creator",
            model_client=model_client,
            workbench=[fs_wb ],
            system_message=

        """You are a Playwright Automation Engineer converting manual test cases to Playwright/Pytest scripts 
    
            Goal: Transform TestCase_BP-*.txt → executable Python test files.
            
            Tasks:
            PHASE 1: Automation script creation
            1. Read ALL TestCase_BP-*.txt files using list_directory & read_file
            
            2. For EACH test case file, create:
              # `test_BP-X.py` - Playwright test script
               `test_BP-X.spec.ts` - TypeScript version      
               When analysis completed:
               "- Output the final script and write them to a new file , "
            "Use the write_file tool from the filesystem server "
            "The file name MUST follow this exact Name format:" 
            "Testscript_BP-*"
            when your task is completed, reply with exactly:SCRIPT Completed
            
            
            """
       )

        automation_agent = AssistantAgent(
            name="AutomationAgent",
            model_client=model_client,
            workbench=[pw_wb, fs_wb],  # <- LIST, not comma-separated args
            system_message=(
                """You are a Playwright automation expert. Take the script file saved by script_creator "
                ***wait for seeing SCRIPT Completed ***
         Use Playwright MCP tools to "
        "execute the smoke test. Execute the automated test step by step and report "
        "results clearly, including any errors or successes."

        "Make sure expected results in the bug are validated in your flow"
        "Important : Use browser_wait_for to wait for success/error messages\n"
        " - Wait for buttons to change state (e.g., 'Applying...' to complete)\n"
        " Always follow the exact timing and waiting instructions provided"
       
                Make sure to prepare a single file for all the executed scripts .
                "- IMPORTANT RULE: If a file with the same name already exists, you MUST OVERWRITE it."
                "- Do NOT create numbered files ."
                "- Use the filesystem MCP `write_file` tool to write or overwrite the file IF it already exists."
        "On failure, record the error and continue; ALWAYS end with: 'TESTING COMPLETE'"

        "Complete ALL steps before saying 'TESTING COMPLETE, Execute each step fully, don't rush to completion"""

            ))



        termination_condition = (
            TextMessageTermination("Task Completed")|
            TextMessageTermination("SCRIPT Completed") |
            TextMessageTermination("TESTING COMPLETE") |
            MaxMessageTermination(max_messages=30))




        team = RoundRobinGroupChat(
            participants=[jira_agent,script_creator,automation_agent],
            termination_condition=termination_condition)

    task_text = """
                    jira_agent:
                    Read all the User stories in the current sprint with the description and 
                acceptance criteria carefully.
                    Prepare all the detailed testcases for each of them and save in the file path.
                    
                    automation_script
                    Read the testcases , convert them in playwright scripts and save.
                    Then, execute them and save the results.
                    If bug found, create a new bug in jira in backlog.
                    """

    await Console(team.run_stream(
            task=task_text
        ))


if __name__ == "__main__":
    asyncio.run(main())
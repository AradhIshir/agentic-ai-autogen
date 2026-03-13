"""
QA Automation Control Panel — Streamlit UI for the AI QA Agent pipeline.
Triggers execution of backend/UStoAutomationBug.py (Autogen + MCP agents).
All outputs (test cases, scripts, execution results, bugs) are saved under AgenticAIAutogen.
"""

import json
import os
import subprocess
import sys

import streamlit as st

# Project root = AgenticAIAutogen (all files saved here)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_SCRIPT = os.path.join(PROJECT_ROOT, "backend", "UStoAutomationBug.py")

# Folders under AgenticAIAutogen where pipeline saves files
FOLDERS = {
    "test_cases": os.path.join(PROJECT_ROOT, "TestCases"),
    "scripts": os.path.join(PROJECT_ROOT, "generated_testscript"),
    "results": os.path.join(PROJECT_ROOT, "ResultReport"),
}


def _get_backend_python() -> str:
    """Use project venv if present (has autogen_agentchat, etc.); else current interpreter."""
    if os.name == "nt":
        venv_python = os.path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe")
    else:
        venv_python = os.path.join(PROJECT_ROOT, ".venv", "bin", "python")
    if os.path.isfile(venv_python):
        return venv_python
    return sys.executable


def run_pipeline(jira_ticket_id: str, step: str = "full") -> tuple[str, str, int]:
    """Run the backend agent pipeline (or single step). step: testcases|automation|execute|bugs|full."""
    env = os.environ.copy()
    env["JIRA_TICKET_ID"] = jira_ticket_id or "EC-298"
    env["PIPELINE_STEP"] = step
    python_exe = _get_backend_python()
    result = subprocess.run(
        [python_exe, BACKEND_SCRIPT],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=3600,
    )
    return (
        result.stdout or "",
        result.stderr or "",
        result.returncode,
    )


def get_final_result_content(jira_id: str) -> str:
    """Read final result text file if it exists."""
    path = os.path.join(FOLDERS["results"], f"result_{jira_id}.txt")
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception:
            pass
    return ""


def get_execution_summary(jira_id: str) -> str:
    """Read execution JSON and return a short summary line."""
    path = os.path.join(FOLDERS["results"], f"execution_{jira_id}.json")
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            t = data.get("total_tests", 0)
            p = data.get("passed_tests", 0)
            f = data.get("failed_tests", 0)
            return f"Tests: {t} total, {p} passed, {f} failed."
        except Exception:
            pass
    return ""


def list_saved_files(jira_id: str) -> list[tuple[str, str]]:
    """Return list of (label, path) for files saved in AgenticAIAutogen for this Jira ID."""
    out = []
    # Test case file
    tc = os.path.join(FOLDERS["test_cases"], f"{jira_id}_Testcase.txt")
    if os.path.isfile(tc):
        out.append(("Test cases", tc))
    # Script
    script = os.path.join(FOLDERS["scripts"], f"Script_{jira_id}_.spec.ts")
    if os.path.isfile(script):
        out.append(("Automation script", script))
    # Result report files
    res_dir = FOLDERS["results"]
    if os.path.isdir(res_dir):
        for name in os.listdir(res_dir):
            if name.startswith(f"result_{jira_id}") or name.startswith(f"execution_{jira_id}") or name.startswith(f"screenshot_{jira_id}") or name.startswith(f"bug_{jira_id}"):
                out.append(("Result / bug / screenshot", os.path.join(res_dir, name)))
    return out


# ----- Step dependency checks (show error if previous file not ready) -----
def _test_cases_exist(jira_id: str) -> bool:
    path = os.path.join(FOLDERS["test_cases"], f"{jira_id}_Testcase.txt")
    return os.path.isfile(path)


def _automation_script_exists(jira_id: str) -> bool:
    path = os.path.join(FOLDERS["scripts"], f"Script_{jira_id}_.spec.ts")
    return os.path.isfile(path)


def _execution_results_exist(jira_id: str) -> bool:
    path = os.path.join(FOLDERS["results"], f"execution_{jira_id}.json")
    return os.path.isfile(path)


# ----- Dashboard: project list with US, test cases created, executed, passed, failed -----
DASHBOARD_CONFIG_PATH = os.path.join(PROJECT_ROOT, "dashboard_config.json")


def _load_project_names() -> dict[str, str]:
    """Load project key -> display name from dashboard_config.json."""
    if os.path.isfile(DASHBOARD_CONFIG_PATH):
        try:
            with open(DASHBOARD_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("projects") or {}
        except Exception:
            pass
    return {}


def _jira_id_to_project_key(jira_id: str) -> str:
    """EC-298 -> EC (prefix before first hyphen)."""
    if "-" in jira_id:
        return jira_id.split("-")[0].strip()
    return jira_id.strip() or "?"


def get_dashboard_data() -> list[dict]:
    """
    Scan TestCases + ResultReport and return one row per project:
    project_name, total_us, test_cases_created, executed, passed, failed.
    """
    project_names = _load_project_names()
    # Collect per–Jira ID: has_testcase_file, total_tests, passed_tests, failed_tests
    jira_metrics: dict[str, dict] = {}

    # Test case files: each file = one User Story
    tc_dir = FOLDERS["test_cases"]
    if os.path.isdir(tc_dir):
        for name in os.listdir(tc_dir):
            if name.endswith("_Testcase.txt"):
                jira_id = name.replace("_Testcase.txt", "").strip()
                if jira_id not in jira_metrics:
                    jira_metrics[jira_id] = {"total_tests": 0, "passed_tests": 0, "failed_tests": 0, "has_testcase": False}
                jira_metrics[jira_id]["has_testcase"] = True

    # Execution results
    res_dir = FOLDERS["results"]
    if os.path.isdir(res_dir):
        for name in os.listdir(res_dir):
            if name.startswith("execution_") and name.endswith(".json"):
                jira_id = name.replace("execution_", "").replace(".json", "").strip()
                if jira_id not in jira_metrics:
                    jira_metrics[jira_id] = {"total_tests": 0, "passed_tests": 0, "failed_tests": 0, "has_testcase": False}
                try:
                    with open(os.path.join(res_dir, name), "r", encoding="utf-8") as f:
                        data = json.load(f)
                    jira_metrics[jira_id]["total_tests"] = data.get("total_tests", 0)
                    jira_metrics[jira_id]["passed_tests"] = data.get("passed_tests", 0)
                    jira_metrics[jira_id]["failed_tests"] = data.get("failed_tests", 0)
                except Exception:
                    pass

    # Aggregate by project key (each Jira ID with any artifact = 1 User Story)
    by_project: dict[str, dict] = {}
    for jira_id, m in jira_metrics.items():
        key = _jira_id_to_project_key(jira_id)
        if key not in by_project:
            by_project[key] = {"total_us": 0, "test_cases_created": 0, "executed": 0, "passed": 0, "failed": 0, "jira_ids": set()}
        by_project[key]["jira_ids"].add(jira_id)
        by_project[key]["test_cases_created"] += m.get("total_tests", 0)
        by_project[key]["executed"] += m.get("total_tests", 0)
        by_project[key]["passed"] += m.get("passed_tests", 0)
        by_project[key]["failed"] += m.get("failed_tests", 0)
    for key in by_project:
        by_project[key]["total_us"] = len(by_project[key]["jira_ids"])
        del by_project[key]["jira_ids"]

    rows = []
    for key in sorted(by_project.keys()):
        r = by_project[key]
        rows.append({
            "Project": project_names.get(key, key),
            "Total # US": r["total_us"],
            "Test cases created": r["test_cases_created"],
            "Executed": r["executed"],
            "Passed": r["passed"],
            "Failed": r["failed"],
        })
    return rows


def _render_dashboard():
    """Dashboard: list of projects with Total US, Test cases created, Executed, Passed, Failed."""
    st.title("QA Dashboard")
    st.markdown("Overview of all projects: User Stories, test cases created, and execution results.")
    st.divider()

    rows = get_dashboard_data()
    if not rows:
        st.info("No project data yet. Run the pipeline for at least one Jira ticket (e.g. EC-298), then come back here.")
        st.caption("Data is read from TestCases and ResultReport folders in AgenticAIAutogen.")
        return

    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.caption("To add or rename projects, edit dashboard_config.json in the project folder (e.g. add a project key and display name like \"Excellence board\").")


def main():
    st.set_page_config(
        page_title="AI Autonomous QA Testing Agent",
        page_icon="🤖",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # ----- Sidebar: switch between Run pipeline and Dashboard -----
    page = st.sidebar.radio(
        "Go to",
        ["Run pipeline", "Dashboard"],
        label_visibility="collapsed",
    )

    if page == "Dashboard":
        _render_dashboard()
        return

    # ----- Run pipeline page -----
    st.title("AI Autonomous QA Testing Agent")
    st.markdown(
        "*Run AI agents to generate tests, automation scripts, execute them and create Jira bugs.*"
    )

    # ----- Simple help (no coding jargon) -----
    with st.expander("📖 How to use (quick guide)", expanded=False):
        st.markdown("""
        **1.** Type your Jira ticket ID in the box (e.g. EC-298).  
        **2.** Click **RUN FULL PIPELINE** to run everything, or click any single button to run the full pipeline.  
        **3.** Wait a few minutes. The logs will appear below.  
        **4.** When you see "Pipeline run finished", you're done.  

        You don't need to use the terminal or write any code. Just use the buttons on this page.
        """)
    st.divider()

    # ----- Input -----
    jira_ticket_id = st.text_input(
        "**Jira Ticket ID**",
        value="EC-298",
        placeholder="e.g. EC-298",
        help="Enter the Jira ticket you want to run tests for (e.g. EC-298).",
    ).strip() or "EC-298"

    # ----- Button section: 4 columns -----
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        btn_test_cases = st.button("Generate Test Cases", use_container_width=True)
    with col2:
        btn_automation = st.button("Generate Automation Scripts", use_container_width=True)
    with col3:
        btn_execute = st.button("Execute Automation", use_container_width=True)
    with col4:
        btn_bugs = st.button("Create Bugs", use_container_width=True)

    # All step buttons trigger the full pipeline (backend runs full RoundRobinGroupChat)
    step_clicked = btn_test_cases or btn_automation or btn_execute or btn_bugs

    # ----- Run Full Pipeline (large button) -----
    st.markdown("---")
    run_full = st.button("RUN FULL PIPELINE", type="primary", use_container_width=True)

    # Check step dependencies: show error if previous file is not ready
    if step_clicked and not run_full:
        if btn_automation and not _test_cases_exist(jira_ticket_id):
            st.error("Test cases are not ready. Please run **Generate Test Cases** first (or use **Run Full Pipeline**).")
            st.stop()
        if btn_execute and not _automation_script_exists(jira_ticket_id):
            st.error("Automation script is not ready. Please run **Generate Automation Scripts** first (or use **Run Full Pipeline**).")
            st.stop()
        if btn_bugs and not _execution_results_exist(jira_ticket_id):
            st.error("Execution results are not ready. Please run **Execute Automation** first (or use **Run Full Pipeline**).")
            st.stop()

    if run_full or step_clicked:
        step = "full"
        if not run_full:
            if btn_test_cases:
                step = "testcases"
            elif btn_automation:
                step = "automation"
            elif btn_execute:
                step = "execute"
            elif btn_bugs:
                step = "bugs"
        with st.spinner("Running QA agent pipeline… This may take several minutes."):
            try:
                stdout, stderr, returncode = run_pipeline(jira_ticket_id, step=step)
            except subprocess.TimeoutExpired:
                stdout = "Pipeline run timed out (1 hour)."
                stderr = ""
                returncode = -1
            except FileNotFoundError:
                stdout = ""
                stderr = f"Backend script not found: {BACKEND_SCRIPT}"
                returncode = -1
            except Exception as e:
                stdout = ""
                stderr = str(e)
                returncode = -1

        # ----- Status -----
        if returncode != 0:
            st.warning(f"Pipeline stopped (exit code {returncode}). Check full log below if needed.")
        else:
            st.success("Pipeline run finished.")

        # ----- Final result (no long execution log) -----
        st.subheader("Final result")
        result_txt = get_final_result_content(jira_ticket_id)
        summary = get_execution_summary(jira_ticket_id)
        if result_txt:
            st.text_area("Execution result", value=result_txt, height=200, disabled=True, label_visibility="collapsed")
        if summary:
            st.caption(summary)
        if not result_txt and not summary:
            st.info("Pipeline completed. Result file may not be created yet (e.g. if execution step did not run).")

        # ----- Saved files (all in AgenticAIAutogen) -----
        st.subheader("Saved files (in AgenticAIAutogen)")
        saved = list_saved_files(jira_ticket_id)
        if saved:
            for label, path in saved:
                rel = os.path.relpath(path, PROJECT_ROOT)
                st.text(f"• {label}: {rel}")
            st.caption(f"All files are saved under: {PROJECT_ROOT}")
        else:
            st.caption("No result files found yet for this Jira ID. They appear after the pipeline runs.")

        # ----- Full log only in expander -----
        full_log = (stdout or "") + ("\n\n--- stderr ---\n" + stderr if stderr else "")
        if full_log:
            with st.expander("View full execution log (for debugging)"):
                st.code(full_log, language="text")
    else:
        # Placeholder when no run yet
        st.subheader("Final result")
        st.info("Click **RUN FULL PIPELINE** or any step button. The final result and saved files will appear here (no long log).")


if __name__ == "__main__":
    main()

"""
QA Automation Control Panel — Streamlit UI for the AI QA Agent pipeline.
Triggers execution of backend/UStoAutomationBug.py (Autogen + MCP agents).
All outputs (test cases, scripts, execution results, bugs) are saved under AgenticAIAutogen.
"""

import streamlit as st

import html
import json
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time

import streamlit.components.v1 as components

_UI_DIR = os.path.dirname(os.path.abspath(__file__))
# Project root = AgenticAIAutogen (all files saved here)
PROJECT_ROOT = os.path.abspath(os.path.join(_UI_DIR, ".."))
BACKEND_SCRIPT = os.path.join(PROJECT_ROOT, "backend", "UStoAutomationBug.py")

for _p in (PROJECT_ROOT, _UI_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Cookie restore runs only on `pages/_Login.py`. If we also prime here before
# `switch_page`, Streamlit can execute both scripts in one run → two CookieManager.get_all()
# calls with the same key "get_all" → StreamlitDuplicateElementKey.

if not st.session_state.get("logged_in"):
    st.switch_page("pages/_Login.py")
    st.stop()

try:
    import pandas as pd
except ImportError:
    pd = None  # type: ignore

try:
    from db import get_connection
except ImportError:
    get_connection = None  # type: ignore

try:
    from db_sync import db_sync_for_step, load_execution_json
except ImportError:
    def db_sync_for_step(ticket_id: str, step: str) -> None:  # type: ignore
        pass

    def load_execution_json(path):  # type: ignore
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

_log = logging.getLogger(__name__)
try:
    from jira_fetch import get_sprint_story_keys
except ImportError:
    get_sprint_story_keys = None  # type: ignore

PROJECT_OPTIONS = {"EC": "Excellence Center", "FAI": "FiresideAI", "AL": "Alcon", "BUR": "Burgess"}
BATCH_SIZE = 5

# Human-readable labels for pipeline steps
STEP_LABELS: dict[str, str] = {
    "testcases":  "Generate Test Cases",
    "automation": "Generate Automation Scripts",
    "execute":    "Execute Automation",
    "bugs":       "Create Bugs",
    "full":       "Full Pipeline",
}

# ── Thread-safe stop signal ────────────────────────────────────────────────
# st.session_state cannot be read from background threads (raises
# "missing ScriptRunContext" and silently returns None), so we use a
# plain threading.Event for the stop flag and a bare variable for the
# current subprocess reference — both are safe to access from any thread.
_stop_event: threading.Event = threading.Event()
_current_process: "subprocess.Popen | None" = None

# Pipeline worker must NEVER touch st.session_state (background thread → ScriptRunContext warnings
# and unreliable writes). Mirror progress here; main thread copies into session_state.
_pipeline_worker_lock = threading.Lock()
_pipeline_worker_ticket: str | None = None
_pipeline_worker_result: dict | None = None


def _sync_pipeline_worker_result_to_session() -> None:
    """Read worker-published result on the main Streamlit thread only."""
    global _pipeline_worker_result
    with _pipeline_worker_lock:
        res = _pipeline_worker_result
        _pipeline_worker_result = None
    if res is not None:
        st.session_state["pipeline_done"] = True
        st.session_state["pipeline_result"] = res
        return
    st.session_state["pipeline_done"] = True
    st.session_state["pipeline_result"] = {
        "batch_progress": [],
        "last_stdout": "",
        "last_stderr": "Pipeline thread ended without a result (killed or internal error).",
        "last_returncode": -1,
        "ticket_list": st.session_state.get("pipeline_ticket_list", []),
        "batches": [],
        "step": st.session_state.get("pipeline_step", "full"),
        "project_key": "",
    }


def _kill_process(proc: subprocess.Popen) -> None:
    """Kill process and all its children (whole process group on Unix)."""
    try:
        if os.name != "nt":
            # Kill the entire process group so MCP/Node child processes also die
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    except (ProcessLookupError, PermissionError):
        pass

# ISHIR brand colors (UI only) — matches ishir.com
ISHIR_YELLOW       = "#FFD400"
ISHIR_YELLOW_HOVER = "#E6C200"
ISHIR_BLACK        = "#000000"
ISHIR_WHITE        = "#FFFFFF"
ISHIR_GRAY_BG      = "#F5F5F5"   # light page background
ISHIR_GRAY_SIDEBAR = "#FAFAFA"   # sidebar background
ISHIR_GRAY_BORDER  = "#E0E0E0"   # subtle borders
ISHIR_TEXT_DARK    = "#111111"   # primary text
ISHIR_TEXT_MUTED   = "#555555"   # captions / secondary text

# Visual theme only. Default: legacy (light sidebar). Corporate B&W: ISHIR_UI_THEME=corporate streamlit run ui/app.py
ISHIR_UI_THEME = os.environ.get("ISHIR_UI_THEME", "legacy").strip().lower()
if ISHIR_UI_THEME not in ("legacy", "corporate"):
    ISHIR_UI_THEME = "legacy"


def _is_corporate_theme() -> bool:
    return ISHIR_UI_THEME == "corporate"


def _theme_tokens() -> dict[str, str]:
    """Token values for _inject_ishir_css. legacy ≈ original Streamlit light UI before corporate refresh."""
    gb, gs, gbd = ISHIR_GRAY_BG, ISHIR_GRAY_SIDEBAR, ISHIR_GRAY_BORDER
    w, td = ISHIR_WHITE, ISHIR_TEXT_DARK
    if ISHIR_UI_THEME == "legacy":
        return {
            "app_bg": gb,
            "main_shadow": "0 1px 4px rgba(0,0,0,0.08)",
            "sidebar_bg": gs,
            "sidebar_edge": gbd,
            "sidebar_fg": td,
            "sidebar_btn_bg": w,
            "sidebar_btn_border": gbd,
            "sidebar_btn_hover_bg": gb,
            "main_border": gbd,
            "hover_fill": gb,
            "metric_bg": gb,
            "metric_edge": gbd,
            "toolbar_hover": gb,
            "header_bg": w,
            "header_fg": td,
            "collapse_btn_bg": w,
            "collapse_btn_border": gbd,
            "hr_sidebar": gbd,
            "metric_extra": "",
        }
    return {
        "app_bg": "#E8EEF5",
        "main_shadow": "0 4px 24px rgba(11,31,58,0.07)",
        "sidebar_bg": "#0B1F3A",
        "sidebar_edge": "rgba(148,163,184,0.22)",
        "sidebar_fg": "#F1F5F9",
        "sidebar_btn_bg": "transparent",
        "sidebar_btn_border": "rgba(248,250,252,0.55)",
        "sidebar_btn_hover_bg": "rgba(255,255,255,0.08)",
        "main_border": "#E2E8F0",
        "hover_fill": "#EEF2F7",
        "metric_bg": "#FFFFFF",
        "metric_edge": "#E2E8F0",
        "toolbar_hover": "#F1F5F9",
        "header_bg": "#0B1F3A",
        "header_fg": "#F8FAFC",
        "collapse_btn_bg": "#1E3A5F",
        "collapse_btn_border": "rgba(148,163,184,0.4)",
        "hr_sidebar": "rgba(148,163,184,0.25)",
        "metric_extra": "border-left: 4px solid #FFD400 !important;",
    }


def _profile_initials(full_name: str) -> str:
    parts = (full_name or "").split()
    letters = "".join(p[0].upper() for p in parts[:2] if p)
    return letters or "?"


# Role-based sidebar navigation (Run pipeline = agent pipeline page)
_NAV_ALL = ["Dashboard", "Test Repository", "Run pipeline"]


def _nav_options_for_role(role: str | None) -> list[str]:
    r = (role or "").strip().lower()
    if r == "admin":
        return list(_NAV_ALL)
    if r == "qa_lead":
        return list(_NAV_ALL)
    if r == "developer":
        return ["Dashboard", "Test Repository"]
    return ["Dashboard", "Test Repository"]


def _inject_sidebar_expand_fallback() -> None:
    """
    When the sidebar is collapsed, Streamlit's built-in expand control can be missing or
    hard to see. Fixed ☰ clicks the native expand control (same as Streamlit's chevron).
    """
    T = _theme_tokens()
    _fab_bg = T["collapse_btn_bg"]
    _fab_border = T["collapse_btn_border"]
    _fab_fg = T["header_fg"]
    components.html(
        f"""
<script>
(function () {{
  try {{
    var doc = window.parent.document;
    if (!doc) return;
    if (doc.getElementById("ishir-sidebar-expand-fab")) return;
    var btn = doc.createElement("button");
    btn.id = "ishir-sidebar-expand-fab";
    btn.type = "button";
    btn.setAttribute("aria-label", "Open navigation menu");
    btn.innerHTML = "&#9776;";
    btn.title = "Open menu";
    var s = btn.style;
    s.cssText =
      "position:fixed!important;top:14px!important;left:10px!important;z-index:1000030!important;" +
      "width:44px!important;height:44px!important;border-radius:10px!important;cursor:pointer!important;" +
      "border:1.5px solid {_fab_border}!important;background:{_fab_bg}!important;color:{_fab_fg}!important;" +
      "font-size:1.4rem!important;line-height:1!important;box-shadow:0 2px 12px rgba(0,0,0,0.15)!important;" +
      "display:none!important;align-items:center!important;justify-content:center!important;padding:0!important;";
    btn.onmouseenter = function () {{
      btn.style.borderColor = "#FFD400";
      btn.style.background = "rgba(255,212,0,0.2)";
    }};
    btn.onmouseleave = function () {{
      btn.style.borderColor = "{_fab_border}";
      btn.style.background = "{_fab_bg}";
    }};
    function clickNativeExpand() {{
      var root = doc;
      var t =
        root.querySelector('[data-testid="stSidebarCollapsedControl"] button') ||
        root.querySelector('[data-testid="stSidebarCollapsedControl"]') ||
        root.querySelector('[data-testid="collapsedControl"] button') ||
        root.querySelector('button[kind="headerNoPadding"]');
      if (t) t.click();
    }}
    btn.onclick = clickNativeExpand;
    function syncVisibility() {{
      var side = doc.querySelector('section[data-testid="stSidebar"]');
      var w = side ? side.getBoundingClientRect().width : 0;
      btn.style.display = w < 12 ? "flex" : "none";
    }}
    doc.body.appendChild(btn);
    syncVisibility();
    setInterval(syncVisibility, 400);
  }} catch (e) {{}}
}})();
</script>
        """,
        height=0,
        width=0,
    )


def _inject_ishir_css():
    """ISHIR brand CSS. Themes: ISHIR_UI_THEME=corporate (navy sidebar, ishir.com-style) | legacy (rollback)."""
    T = _theme_tokens()
    _corp = _is_corporate_theme()
    _radio_accent = ""
    if _corp:
        _radio_accent = """
        section[data-testid="stSidebar"] [role="radiogroup"] > div > label {
            border-left: 3px solid transparent !important;
            border-radius: 8px !important;
            margin: 2px 0 !important;
            padding: 0.45rem 0.5rem 0.45rem 0.55rem !important;
        }
        section[data-testid="stSidebar"] [role="radiogroup"] [aria-checked="true"] {
            background: rgba(255,212,0,0.14) !important;
            border-left: 3px solid #FFD400 !important;
        }
        """
    st.markdown(
        f"""
        <style>
        /* ── Global: white background, black text, larger base font ──────── */
        html, body {{
            font-size: 17px !important;
        }}
        .stApp {{
            background-color: {T["app_bg"]} !important;
            color: {ISHIR_TEXT_DARK} !important;
            font-family: 'Inter', 'Segoe UI', Arial, sans-serif !important;
            font-size: 1rem !important;
        }}

        /* ── Main content area ────────────────────────────────────────────── */
        .main .block-container {{
            background-color: {ISHIR_WHITE} !important;
            border-radius: 12px !important;
            padding: 2rem 2.5rem !important;
            box-shadow: {T["main_shadow"]} !important;
        }}

        /* ── Sidebar ──────────────────────────────────────────────────────── */
        section[data-testid="stSidebar"] {{
            background-color: {T["sidebar_bg"]} !important;
            border-right: 1px solid {T["sidebar_edge"]} !important;
        }}
        section[data-testid="stSidebar"] * {{
            color: {T["sidebar_fg"]} !important;
        }}
        section[data-testid="stSidebar"] .stRadio label {{
            color: {T["sidebar_fg"]} !important;
            font-weight: 500 !important;
        }}
        section[data-testid="stSidebar"] hr {{
            border-color: {T["hr_sidebar"]} !important;
        }}
        {_radio_accent}

        /* ── Text & headings ──────────────────────────────────────────────── */
        h1, h2, h3, h4, h5, h6 {{
            color: {ISHIR_TEXT_DARK} !important;
            font-weight: 700 !important;
        }}
        p, li, span, div, label {{
            color: {ISHIR_TEXT_DARK} !important;
        }}
        .stCaption, caption, small {{
            color: {ISHIR_TEXT_MUTED} !important;
        }}

        /* ── Primary buttons: ISHIR yellow ────────────────────────────────── */
        .stButton > button[kind="primary"],
        .stButton > button[data-testid="baseButton-primary"] {{
            background: {ISHIR_YELLOW} !important;
            color: {ISHIR_BLACK} !important;
            font-weight: 700 !important;
            border: none !important;
            border-radius: 6px !important;
            letter-spacing: 0.3px !important;
        }}
        .stButton > button[kind="primary"]:hover,
        .stButton > button[data-testid="baseButton-primary"]:hover {{
            background: {ISHIR_YELLOW_HOVER} !important;
            color: {ISHIR_BLACK} !important;
        }}

        /* ── Primary button (RUN FULL PIPELINE): ISHIR yellow ────────────── */
        .stButton > button[kind="primary"],
        .stButton > button[data-testid="baseButton-primary"],
        button[kind="primary"] {{
            background: {ISHIR_YELLOW} !important;
            color: {ISHIR_BLACK} !important;
            font-weight: 700 !important;
            border: none !important;
            border-radius: 6px !important;
        }}
        .stButton > button[kind="primary"]:hover,
        .stButton > button[data-testid="baseButton-primary"]:hover {{
            background: {ISHIR_YELLOW_HOVER} !important;
            color: {ISHIR_BLACK} !important;
        }}

        /* ── Step buttons (non-primary): white background, black bold text ── */
        .stButton > button:not([kind="primary"]),
        .stButton > button[kind="secondary"],
        .stButton > button[data-testid="baseButton-secondary"] {{
            background: {ISHIR_WHITE} !important;
            color: {ISHIR_BLACK} !important;
            font-weight: 700 !important;
            border: 1.5px solid {T["main_border"]} !important;
            border-radius: 6px !important;
        }}
        .stButton > button:not([kind="primary"]):hover {{
            background: {T["hover_fill"]} !important;
            border-color: {ISHIR_YELLOW} !important;
            color: {ISHIR_BLACK} !important;
        }}

        /* ── Sidebar buttons stay neutral ─────────────────────────────────── */
        section[data-testid="stSidebar"] .stButton > button {{
            background: {T["sidebar_btn_bg"]} !important;
            color: {T["sidebar_fg"]} !important;
            font-weight: 600 !important;
            border: 1.5px solid {T["sidebar_btn_border"]} !important;
            border-radius: 6px !important;
        }}
        section[data-testid="stSidebar"] .stButton > button:hover {{
            border-color: {ISHIR_YELLOW} !important;
            background: {T["sidebar_btn_hover_bg"]} !important;
        }}

        /* ── Input fields ─────────────────────────────────────────────────── */
        .stTextInput input, .stSelectbox select, textarea {{
            background-color: {ISHIR_WHITE} !important;
            color: {ISHIR_TEXT_DARK} !important;
            border: 1.5px solid {T["main_border"]} !important;
            border-radius: 6px !important;
        }}
        .stTextInput input:focus, .stSelectbox select:focus {{
            border-color: {ISHIR_YELLOW} !important;
            box-shadow: 0 0 0 2px rgba(255,212,0,0.2) !important;
        }}

        /* ── Expanders: force white on every element Streamlit uses ──────── */

        /* Outer wrapper */
        [data-testid="stExpander"] {{
            background-color: {ISHIR_WHITE} !important;
            border: 1px solid {T["main_border"]} !important;
            border-radius: 8px !important;
            overflow: hidden !important;
        }}

        /* The native <details> element Streamlit renders */
        details {{
            background-color: {ISHIR_WHITE} !important;
        }}

        /* The native <summary> element (the clickable header row) */
        details > summary {{
            background-color: {ISHIR_WHITE} !important;
            color: {ISHIR_TEXT_DARK} !important;
            list-style: none !important;
        }}
        details > summary:hover {{
            background-color: {T["hover_fill"]} !important;
            color: {ISHIR_TEXT_DARK} !important;
        }}

        /* Streamlit injects an inner div/span with its own background — override all */
        details > summary > div,
        details > summary > div > *,
        details > summary span,
        details > summary p {{
            background-color: {ISHIR_WHITE} !important;
            color: {ISHIR_TEXT_DARK} !important;
        }}
        details > summary:hover > div,
        details > summary:hover span {{
            background-color: {T["hover_fill"]} !important;
            color: {ISHIR_TEXT_DARK} !important;
        }}

        /* Content area below the summary */
        details > div,
        [data-testid="stExpanderDetails"] {{
            background-color: {ISHIR_WHITE} !important;
        }}

        /* Fallback legacy class names */
        .streamlit-expanderHeader,
        .streamlit-expanderHeader * {{
            background-color: {ISHIR_WHITE} !important;
            color: {ISHIR_TEXT_DARK} !important;
        }}
        .streamlit-expanderContent {{
            background-color: {ISHIR_WHITE} !important;
        }}

        /* ── Metric cards ─────────────────────────────────────────────────── */
        [data-testid="stMetric"] {{
            background: {T["metric_bg"]} !important;
            border-radius: 8px !important;
            padding: 0.75rem 1rem !important;
            border: 1px solid {T["metric_edge"]} !important;
            {T["metric_extra"]}
        }}
        [data-testid="stMetricLabel"] {{
            color: {ISHIR_TEXT_MUTED} !important;
        }}
        [data-testid="stMetricValue"] {{
            color: {ISHIR_TEXT_DARK} !important;
            font-weight: 700 !important;
        }}

        /* ── Tabs ─────────────────────────────────────────────────────────── */
        .stTabs [data-testid="stTab"] {{
            color: {ISHIR_TEXT_MUTED} !important;
            font-weight: 500 !important;
        }}
        .stTabs [aria-selected="true"] {{
            color: {ISHIR_TEXT_DARK} !important;
            border-bottom: 2px solid {ISHIR_YELLOW} !important;
            font-weight: 700 !important;
        }}

        /* ── Dataframe / tables — including toolbar, search, sort buttons ── */
        [data-testid="stDataFrame"] {{
            border: 1px solid {T["main_border"]} !important;
            border-radius: 8px !important;
        }}
        /* Toolbar: always visible, floated above the table top-right ──────── */
        [data-testid="stElementToolbar"] {{
            opacity: 1 !important;
            visibility: visible !important;
            position: absolute !important;
            top: -52px !important;        /* sit above the table, not inside it */
            right: 0 !important;
            z-index: 9999 !important;
            background-color: {ISHIR_WHITE} !important;
            border: 1.5px solid {T["main_border"]} !important;
            border-radius: 8px !important;
            padding: 4px 8px !important;
            box-shadow: 0 2px 8px rgba(0,0,0,0.12) !important;
            display: flex !important;
            align-items: center !important;
            gap: 2px !important;
        }}
        /* Ensure the dataframe wrapper has relative positioning for the offset */
        [data-testid="stDataFrame"] {{
            position: relative !important;
            overflow: visible !important;
        }}
        /* Icon buttons */
        [data-testid="stElementToolbar"] button {{
            background-color: {ISHIR_WHITE} !important;
            color: {ISHIR_TEXT_DARK} !important;
            width: 34px !important;
            height: 34px !important;
            border-radius: 6px !important;
            padding: 5px !important;
            border: none !important;
        }}
        [data-testid="stElementToolbar"] button svg {{
            width: 18px !important;
            height: 18px !important;
            color: {ISHIR_TEXT_DARK} !important;
            fill: {ISHIR_TEXT_DARK} !important;
        }}
        [data-testid="stElementToolbar"] button:hover {{
            background-color: {T["toolbar_hover"]} !important;
            outline: 1.5px solid {ISHIR_YELLOW} !important;
        }}
        /* Search input inside toolbar */
        [data-testid="stElementToolbar"] input {{
            background-color: {ISHIR_WHITE} !important;
            color: {ISHIR_TEXT_DARK} !important;
            border: 1px solid {T["main_border"]} !important;
            border-radius: 4px !important;
        }}
        /* Glide-data-grid sort arrows */
        .dvn-scroller,
        .dvn-scroller * {{
            scrollbar-color: {T["main_border"]} {ISHIR_WHITE} !important;
        }}

        /* ── Info / warning / success boxes ───────────────────────────────── */
        [data-testid="stAlert"] {{
            border-radius: 8px !important;
        }}

        /* ── Dividers ─────────────────────────────────────────────────────── */
        hr {{
            border-color: {T["main_border"]} !important;
        }}

        /* ── Selectbox trigger — white background, dark text ──────────────── */
        [data-testid="stSelectbox"] > div > div {{
            background-color: {ISHIR_WHITE} !important;
            color: {ISHIR_TEXT_DARK} !important;
            border: 1.5px solid {T["main_border"]} !important;
            border-radius: 6px !important;
        }}
        [data-testid="stSelectbox"] span,
        [data-testid="stSelectbox"] div {{
            color: {ISHIR_TEXT_DARK} !important;
        }}
        [data-testid="stSelectbox"] svg {{
            fill: {ISHIR_TEXT_DARK} !important;
        }}

        /* ── Dropdown popup — BaseWeb portal (renders outside stSelectbox) ── */
        div[data-baseweb="popover"],
        div[data-baseweb="popover"] * {{
            background-color: {ISHIR_WHITE} !important;
            color: {ISHIR_TEXT_DARK} !important;
        }}
        div[data-baseweb="menu"],
        div[data-baseweb="menu"] * {{
            background-color: {ISHIR_WHITE} !important;
            color: {ISHIR_TEXT_DARK} !important;
        }}
        div[data-baseweb="select"] div,
        div[data-baseweb="select"] span {{
            background-color: {ISHIR_WHITE} !important;
            color: {ISHIR_TEXT_DARK} !important;
        }}
        /* Individual option items */
        [role="listbox"],
        [role="listbox"] * {{
            background-color: {ISHIR_WHITE} !important;
            color: {ISHIR_TEXT_DARK} !important;
        }}
        [role="option"] {{
            background-color: {ISHIR_WHITE} !important;
            color: {ISHIR_TEXT_DARK} !important;
        }}
        [role="option"]:hover,
        [aria-selected="true"][role="option"] {{
            background-color: {T["hover_fill"]} !important;
            color: {ISHIR_TEXT_DARK} !important;
        }}
        /* Scrollbar in dropdown */
        div[data-baseweb="popover"] ::-webkit-scrollbar-track {{
            background: {ISHIR_WHITE} !important;
        }}
        /* Generic select fallback */
        select, select option {{
            background-color: {ISHIR_WHITE} !important;
            color: {ISHIR_TEXT_DARK} !important;
        }}

        /* ── App header + collapsed sidebar expand control ─────────────────── */
        header[data-testid="stHeader"] {{
            overflow: visible !important;
            background-color: {T["header_bg"]} !important;
            border-bottom: 1px solid {T["main_border"]} !important;
        }}
        header[data-testid="stHeader"] * {{
            color: {T["header_fg"]} !important;
        }}
        header[data-testid="stHeader"] svg {{
            fill: {T["header_fg"]} !important;
        }}
        [data-testid="stDecoration"] {{
            overflow: visible !important;
        }}
        [data-testid="stSidebarCollapsedControl"] {{
            display: flex !important;
            visibility: visible !important;
            opacity: 1 !important;
            z-index: 1000025 !important;
            position: relative !important;
        }}
        [data-testid="stSidebarCollapsedControl"] button {{
            display: flex !important;
            visibility: visible !important;
            opacity: 1 !important;
            background-color: {T["collapse_btn_bg"]} !important;
            color: {T["header_fg"]} !important;
            border: 1.5px solid {T["collapse_btn_border"]} !important;
            border-radius: 8px !important;
            box-shadow: 0 2px 10px rgba(0,0,0,0.12) !important;
        }}
        [data-testid="collapsedControl"] {{
            z-index: 1000025 !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    _inject_sidebar_expand_fallback()

# Folders under AgenticAIAutogen where pipeline saves files
FOLDERS = {
    "test_cases": os.path.join(PROJECT_ROOT, "TestCases"),
    "scripts": os.path.join(PROJECT_ROOT, "generated_testscript"),
    "results": os.path.join(PROJECT_ROOT, "ResultReport"),
}


def _ensure_workspace_dirs() -> None:
    """Ensure pipeline output folders exist; create each only if missing (does not touch existing dirs)."""
    for name in ("TestCases", "generated_testscript", "ResultReport"):
        path = os.path.join(PROJECT_ROOT, name)
        if not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)


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
    _ensure_workspace_dirs()
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


def _start_pipeline_process(jira_ticket_id: str, step: str = "full") -> subprocess.Popen:
    """Start pipeline as a subprocess in its own process group so the whole tree can be killed."""
    _ensure_workspace_dirs()
    env = os.environ.copy()
    env["JIRA_TICKET_ID"] = jira_ticket_id or "EC-298"
    env["PIPELINE_STEP"] = step
    python_exe = _get_backend_python()
    kwargs: dict = dict(
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    if os.name != "nt":
        kwargs["start_new_session"] = True   # new process group → killpg works
    return subprocess.Popen([python_exe, BACKEND_SCRIPT], **kwargs)


def _pipeline_worker(ticket_list: list[str], step: str, batches: list[list[str]], project_key: str) -> None:
    """Background thread: run pipeline for each ticket, use _stop_event (thread-safe) for stop signal."""
    global _current_process, _pipeline_worker_ticket, _pipeline_worker_result
    stopped = False
    result_dict: dict | None = None
    try:
        batch_progress: list[str] = []
        last_stdout, last_stderr, last_returncode = "", "", 0
        for batch_idx, batch in enumerate(batches):
            if _stop_event.is_set():
                stopped = True
                break
            if len(batches) > 1:
                batch_progress.append(f"Batch {batch_idx + 1}: {', '.join(batch)}")
            for ticket_id in batch:
                if _stop_event.is_set():
                    stopped = True
                    break
                with _pipeline_worker_lock:
                    _pipeline_worker_ticket = ticket_id
                try:
                    process = _start_pipeline_process(ticket_id, step=step)
                    _current_process = process
                    while process.poll() is None:
                        if _stop_event.is_set():
                            stopped = True
                            _kill_process(process)
                            last_stdout, last_stderr, last_returncode = "Pipeline stopped by user.", "", -1
                            break
                        time.sleep(0.3)
                    else:
                        last_stdout = (process.stdout and process.stdout.read()) or ""
                        last_stderr = (process.stderr and process.stderr.read()) or ""
                        last_returncode = process.returncode or 0
                except FileNotFoundError:
                    last_stdout, last_stderr, last_returncode = "", f"Backend script not found: {BACKEND_SCRIPT}", -1
                except Exception as e:
                    last_stdout, last_stderr, last_returncode = "", str(e), -1
                _current_process = None
                if not stopped and last_returncode == 0:
                    try:
                        db_sync_for_step(ticket_id, step)
                    except Exception:
                        _log.exception("db_sync_for_step failed for %s step=%s", ticket_id, step)
                if stopped:
                    break
            if len(batches) > 1 and not stopped:
                batch_progress.append(f"Batch {batch_idx + 1} completed.")
            if stopped:
                break
        result_dict = {
            "batch_progress": batch_progress,
            "last_stdout": last_stdout,
            "last_stderr": last_stderr,
            "last_returncode": last_returncode,
            "ticket_list": ticket_list,
            "batches": batches,
            "step": step,
            "project_key": project_key,
        }
    except Exception as e:
        result_dict = {
            "batch_progress": [],
            "last_stdout": "",
            "last_stderr": str(e),
            "last_returncode": -1,
            "ticket_list": ticket_list,
            "batches": batches,
            "step": step,
            "project_key": project_key,
        }
    finally:
        _current_process = None
        with _pipeline_worker_lock:
            _pipeline_worker_ticket = None
            if result_dict is not None:
                _pipeline_worker_result = result_dict


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
            data = load_execution_json(path)
            if not data:
                return ""
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
    script = os.path.join(FOLDERS["scripts"], f"Script_{jira_id}.spec.ts")
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
    """Check file system first, then DB — test cases must exist before scripts can be generated."""
    if os.path.isfile(os.path.join(FOLDERS["test_cases"], f"{jira_id}_Testcase.txt")):
        return True
    if get_connection is not None:
        try:
            with get_connection() as conn:
                cur = conn.execute("SELECT COUNT(*) FROM test_cases WHERE jira_id = ?", (jira_id,))
                return (cur.fetchone()[0] or 0) > 0
        except Exception:
            pass
    return False


def _automation_script_exists(jira_id: str) -> bool:
    """Check file system first, then DB — scripts must exist before execution."""
    if os.path.isfile(os.path.join(FOLDERS["scripts"], f"Script_{jira_id}.spec.ts")):
        return True
    if get_connection is not None:
        try:
            with get_connection() as conn:
                cur = conn.execute(
                    "SELECT COUNT(*) FROM automation_scripts WHERE testcase_id IN "
                    "(SELECT testcase_id FROM test_cases WHERE jira_id = ?)",
                    (jira_id,),
                )
                return (cur.fetchone()[0] or 0) > 0
        except Exception:
            pass
    return False


def _execution_results_exist(jira_id: str) -> bool:
    """Check file system first, then DB — execution results must exist before bug creation."""
    if os.path.isfile(os.path.join(FOLDERS["results"], f"execution_{jira_id}.json")):
        return True
    if get_connection is not None:
        try:
            with get_connection() as conn:
                cur = conn.execute(
                    "SELECT COUNT(*) FROM execution_results WHERE testcase_id IN "
                    "(SELECT testcase_id FROM test_cases WHERE jira_id = ?)",
                    (jira_id,),
                )
                return (cur.fetchone()[0] or 0) > 0
        except Exception:
            pass
    return False


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


def _load_project_names_from_db() -> dict[str, str]:
    """project_key -> project_name from qa_testing.db projects table."""
    if get_connection is None:
        return {}
    try:
        with get_connection() as conn:
            cur = conn.execute("SELECT project_key, project_name FROM projects ORDER BY project_key")
            return {str(r[0]): (r[1] or r[0]) for r in cur.fetchall()}
    except Exception:
        return {}


def _jira_id_to_project_key(jira_id: str) -> str:
    """EC-298 -> EC (prefix before first hyphen)."""
    if "-" in jira_id:
        return jira_id.split("-")[0].strip()
    return jira_id.strip() or "?"


def get_dashboard_data() -> list[dict]:
    """
    One row per User Story (jira_id) from qa_testing.db:
    project display name, US#, test_cases rows, distinct executed/passed/failed (execution_results).
    Project labels: merge projects table + dashboard_config.json (JSON overrides DB).
    """
    if get_connection is None:
        return []
    # DB first, then dashboard_config.json on top (custom names win)
    project_names = {**_load_project_names_from_db(), **_load_project_names()}
    try:
        with get_connection() as conn:
            cur = conn.execute(
                """
                WITH jira_list AS (
                    SELECT DISTINCT jira_id FROM user_stories
                    UNION
                    SELECT DISTINCT jira_id FROM test_cases
                ),
                us_map AS (
                    SELECT jira_id, MAX(project_key) AS project_key
                    FROM user_stories
                    GROUP BY jira_id
                )
                SELECT
                    jl.jira_id,
                    um.project_key,
                    COALESCE((
                        SELECT COUNT(DISTINCT t.testcase_id)
                        FROM test_cases t
                        WHERE t.jira_id = jl.jira_id
                    ), 0),
                    COALESCE((
                        SELECT COUNT(DISTINCT t.testcase_id)
                        FROM test_cases t
                        INNER JOIN execution_results e ON e.testcase_id = t.testcase_id
                        WHERE t.jira_id = jl.jira_id
                    ), 0),
                    COALESCE((
                        SELECT COUNT(DISTINCT t.testcase_id)
                        FROM test_cases t
                        INNER JOIN execution_results e ON e.testcase_id = t.testcase_id
                            AND UPPER(TRIM(COALESCE(e.execution_status, ''))) = 'PASSED'
                        WHERE t.jira_id = jl.jira_id
                    ), 0),
                    COALESCE((
                        SELECT COUNT(DISTINCT t.testcase_id)
                        FROM test_cases t
                        INNER JOIN execution_results e ON e.testcase_id = t.testcase_id
                            AND UPPER(TRIM(COALESCE(e.execution_status, ''))) = 'FAILED'
                        WHERE t.jira_id = jl.jira_id
                    ), 0)
                FROM jira_list jl
                LEFT JOIN us_map um ON um.jira_id = jl.jira_id
                ORDER BY COALESCE(um.project_key, ''), jl.jira_id
                """
            )
            rows: list[dict] = []
            for r in cur.fetchall():
                jid = r[0]
                pkey_raw = r[1]
                n_created, n_executed, n_passed, n_failed = (int(r[2] or 0), int(r[3] or 0), int(r[4] or 0), int(r[5] or 0))
                pkey = (pkey_raw or "").strip() or _jira_id_to_project_key(jid)
                display = project_names.get(pkey, pkey)
                rows.append(
                    {
                        "Project": display,
                        "US#": jid,
                        "Test cases created": n_created,
                        "Executed": n_executed,
                        "Passed": n_passed,
                        "Failed": n_failed,
                    }
                )
            return rows
    except Exception:
        return []


def _style_dashboard_table(rows: list[dict]):
    """ISHIR yellow header + larger type for QA Dashboard table only (pandas Styler)."""
    if pd is None or not rows:
        return None
    try:
        df = pd.DataFrame(rows)
        sty = (
            df.style.hide(axis="index")
            .set_table_styles(
                [
                    {
                        "selector": "table",
                        "props": [
                            ("border", f"2px solid {ISHIR_YELLOW}"),
                            ("border-collapse", "collapse"),
                        ],
                    },
                    {
                        "selector": "thead th",
                        "props": [
                            ("background-color", ISHIR_YELLOW),
                            ("color", ISHIR_BLACK),
                            ("font-weight", "700"),
                            ("font-size", "1.125rem"),
                            ("padding", "0.65rem 1rem"),
                            ("border-bottom", f"3px solid {ISHIR_BLACK}"),
                            ("text-align", "left"),
                        ],
                    },
                    {
                        "selector": "tbody td",
                        "props": [
                            ("font-size", "1.0625rem"),
                            ("padding", "0.65rem 1rem"),
                            ("line-height", "1.45"),
                        ],
                    },
                    {
                        "selector": "tbody tr td:first-child",
                        "props": [
                            ("border-left", f"4px solid {ISHIR_YELLOW}"),
                            ("padding-left", "0.85rem"),
                        ],
                    },
                ],
                overwrite=False,
            )
        )
        return sty
    except Exception:
        return None


def _render_dashboard():
    """Dashboard: one row per User Story (US#) with Test cases created, Executed, Passed, Failed."""
    st.title("QA Dashboard")
    st.markdown("Overview of all projects: User Stories, test cases created, and execution results.")
    st.divider()

    if get_connection is None:
        st.error("Database connection is not available. Ensure `db.py` exists and `db/qa_testing.db` is reachable.")
        return

    rows = get_dashboard_data()
    if not rows:
        st.info(
            "No dashboard rows yet. Populate **user_stories** and **test_cases** in `qa_testing.db` "
            "(e.g. run the pipeline and DB sync, or use testcase sync)."
        )
        st.caption("Dashboard reads only from the QA SQLite database — not from TestCases/ResultReport folders.")
        return

    styled = _style_dashboard_table(rows)
    if styled is not None:
        st.dataframe(styled, width="stretch", hide_index=True)
    else:
        st.dataframe(rows, width="stretch", hide_index=True)


# ----- Test Repository: DB-backed QA artifact queries -----
def _repo_projects():
    """List project_key, project_name from projects table."""
    if get_connection is None:
        return []
    try:
        with get_connection() as conn:
            cur = conn.execute("SELECT project_key, project_name FROM projects ORDER BY project_key")
            return [{"project_key": r[0], "project_name": r[1] or r[0]} for r in cur.fetchall()]
    except Exception:
        return []


def _repo_user_stories(project_key: str):
    """List user stories for project: jira_id, title."""
    if get_connection is None:
        return []
    try:
        with get_connection() as conn:
            cur = conn.execute(
                "SELECT jira_id, title FROM user_stories WHERE project_key = ? ORDER BY jira_id",
                (project_key,),
            )
            return [{"jira_id": r[0], "title": r[1] or r[0]} for r in cur.fetchall()]
    except Exception:
        return []


def _format_repo_us_dropdown_label(jira_id: str, title: str | None) -> str:
    """User Story selectbox label: show jira_id once; avoid 'EC-298: EC-298' when title duplicates the key."""
    jid = (jira_id or "").strip()
    t = (title or "").strip()
    if not t or t == jid:
        return jid
    if t.startswith(jid):
        rest = t[len(jid):].lstrip(" \t—:–-")
        if not rest or rest.strip() == jid:
            return jid
        clip = rest[:40] + ("…" if len(rest) > 40 else "")
        return f"{jid}: {clip}"
    clip = t[:40] + ("…" if len(t) > 40 else "")
    return f"{jid}: {clip}"


def _dedupe_user_stories_by_jira(rows: list[dict]) -> list[dict]:
    """Keep one row per jira_id (first wins) so the dropdown has no duplicate keys."""
    by_jira: dict[str, dict] = {}
    for u in rows:
        jid = u.get("jira_id") or ""
        if jid and jid not in by_jira:
            by_jira[jid] = u
    return sorted(by_jira.values(), key=lambda x: x["jira_id"])


def _repo_metrics(project_key: str, jira_id_filter: str | None, search: str):
    """Return total_us, total_tc, automated, passed, failed for filters."""
    if get_connection is None:
        return {"total_us": 0, "total_tc": 0, "automated": 0, "passed": 0, "failed": 0}
    try:
        with get_connection() as conn:
            cur = conn.execute(
                "SELECT COUNT(DISTINCT jira_id) FROM user_stories WHERE project_key = ?",
                (project_key,),
            )
            total_us = cur.fetchone()[0] or 0
            base_sql = """
                SELECT tc.id FROM test_cases tc
                INNER JOIN user_stories us ON us.jira_id = tc.jira_id AND us.project_key = ?
            """
            params = [project_key]
            if jira_id_filter:
                base_sql += " AND tc.jira_id = ?"
                params.append(jira_id_filter)
            if search and search.strip():
                base_sql += " AND (tc.testcase_id LIKE ? OR tc.title LIKE ? OR tc.description LIKE ? OR tc.jira_id LIKE ?)"
                q = f"%{search.strip()}%"
                params.extend([q, q, q, q])
            cur = conn.execute("SELECT COUNT(*) FROM (" + base_sql + ") x", params)
            total_tc = cur.fetchone()[0] or 0
            automated_sql = """SELECT COUNT(DISTINCT tc.id) FROM test_cases tc
                INNER JOIN user_stories us ON us.jira_id = tc.jira_id AND us.project_key = ?
                INNER JOIN automation_scripts a ON a.testcase_id = tc.testcase_id"""
            if jira_id_filter:
                automated_sql += " WHERE tc.jira_id = ?"
            cur = conn.execute(automated_sql, [project_key] + ([jira_id_filter] if jira_id_filter else []))
            automated = cur.fetchone()[0] or 0
            exec_sql = """SELECT COUNT(DISTINCT tc.id) FROM test_cases tc
                INNER JOIN user_stories us ON us.jira_id = tc.jira_id AND us.project_key = ?
                INNER JOIN execution_results e ON e.testcase_id = tc.testcase_id"""
            if jira_id_filter:
                exec_sql += " WHERE tc.jira_id = ?"
            cur = conn.execute(exec_sql, [project_key] + ([jira_id_filter] if jira_id_filter else []))
            executed = cur.fetchone()[0] or 0
            passed_sql = """SELECT COUNT(DISTINCT tc.id) FROM test_cases tc
                INNER JOIN user_stories us ON us.jira_id = tc.jira_id AND us.project_key = ?
                INNER JOIN execution_results e ON e.testcase_id = tc.testcase_id AND e.execution_status = 'PASSED'"""
            if jira_id_filter:
                passed_sql += " WHERE tc.jira_id = ?"
            cur = conn.execute(passed_sql, [project_key] + ([jira_id_filter] if jira_id_filter else []))
            passed = cur.fetchone()[0] or 0
            failed_sql = """SELECT COUNT(DISTINCT tc.id) FROM test_cases tc
                INNER JOIN user_stories us ON us.jira_id = tc.jira_id AND us.project_key = ?
                INNER JOIN execution_results e ON e.testcase_id = tc.testcase_id AND e.execution_status = 'FAILED'"""
            if jira_id_filter:
                failed_sql += " WHERE tc.jira_id = ?"
            cur = conn.execute(failed_sql, [project_key] + ([jira_id_filter] if jira_id_filter else []))
            failed = cur.fetchone()[0] or 0
            return {"total_us": total_us, "total_tc": total_tc, "automated": automated, "passed": passed, "failed": failed}
    except Exception:
        return {"total_us": 0, "total_tc": 0, "automated": 0, "passed": 0, "failed": 0}


def _repo_test_cases_grouped(project_key: str, jira_id_filter: str | None, search: str):
    """Return list of user stories, each with list of test cases (testcase_id, title, priority, status, etc.)."""
    if get_connection is None:
        return []
    try:
        with get_connection() as conn:
            conn.row_factory = sqlite3.Row
            base_sql = """
                SELECT tc.id, tc.testcase_id, tc.jira_id, tc.title, tc.description, tc.priority, tc.status, us.title AS us_title
                FROM test_cases tc
                INNER JOIN user_stories us ON us.jira_id = tc.jira_id AND us.project_key = ?
            """
            params = [project_key]
            if jira_id_filter:
                base_sql += " AND tc.jira_id = ?"
                params.append(jira_id_filter)
            if search and search.strip():
                base_sql += " AND (tc.testcase_id LIKE ? OR tc.title LIKE ? OR tc.description LIKE ? OR tc.jira_id LIKE ?)"
                q = f"%{search.strip()}%"
                params.extend([q, q, q, q])
            base_sql += " ORDER BY us.jira_id, tc.testcase_id"
            cur = conn.execute(base_sql, params)
            rows = [dict(r) for r in cur.fetchall()]
            us_map = {}
            for r in rows:
                jira_id = r["jira_id"]
                if jira_id not in us_map:
                    us_map[jira_id] = {"jira_id": jira_id, "us_title": r["us_title"] or jira_id, "test_cases": []}
                tc_id = r["testcase_id"]
                cur2 = conn.execute("SELECT 1 FROM automation_scripts WHERE testcase_id = ? LIMIT 1", (tc_id,))
                automation_status = "Yes" if cur2.fetchone() else "No"
                cur2 = conn.execute(
                    "SELECT execution_status, execution_date FROM execution_results WHERE testcase_id = ? ORDER BY execution_date DESC LIMIT 1",
                    (tc_id,),
                )
                last_row = cur2.fetchone()
                last_exec_status = last_row[0] if last_row else "—"
                last_exec_date = last_row[1] if last_row else "—"
                us_map[jira_id]["test_cases"].append({
                    "id": r["id"], "testcase_id": tc_id, "jira_id": jira_id, "title": r["title"] or "",
                    "description": r["description"] or "", "priority": r["priority"] or "", "status": r["status"] or "NOT_RUN",
                    "automation_status": automation_status, "last_execution_status": last_exec_status, "last_execution_date": last_exec_date,
                })
            return list(us_map.values())
    except Exception:
        return []


def _repo_steps(testcase_id: str):
    """Steps for test case from test_case_steps."""
    if get_connection is None:
        return []
    try:
        with get_connection() as conn:
            cur = conn.execute(
                "SELECT step_number, step_action, expected_result FROM test_case_steps WHERE testcase_id = ? ORDER BY step_number",
                (testcase_id,),
            )
            return [{"step_number": r[0], "step_action": r[1] or "", "expected_result": r[2] or ""} for r in cur.fetchall()]
    except Exception:
        return []


def _repo_automation(testcase_id: str):
    if get_connection is None:
        return []
    try:
        with get_connection() as conn:
            cur = conn.execute(
                "SELECT script_name, script_path, framework, created_date FROM automation_scripts WHERE testcase_id = ?",
                (testcase_id,),
            )
            return [{"script_name": r[0], "script_path": r[1], "framework": r[2], "created_date": r[3]} for r in cur.fetchall()]
    except Exception:
        return []


def _repo_executions(testcase_id: str):
    if get_connection is None:
        return []
    try:
        with get_connection() as conn:
            cur = conn.execute(
                "SELECT execution_status, execution_logs, report_path, execution_date FROM execution_results WHERE testcase_id = ? ORDER BY execution_date DESC",
                (testcase_id,),
            )
            return [{"execution_status": r[0], "execution_logs": r[1], "report_path": r[2], "execution_date": r[3]} for r in cur.fetchall()]
    except Exception:
        return []


def _repo_bugs(testcase_id: str):
    if get_connection is None:
        return []
    try:
        with get_connection() as conn:
            cur = conn.execute(
                "SELECT jira_bug_id, bug_status, created_date FROM bugs WHERE testcase_id = ?",
                (testcase_id,),
            )
            return [{"jira_bug_id": r[0], "bug_status": r[1], "created_date": r[2]} for r in cur.fetchall()]
    except Exception:
        return []


def _render_test_repository():
    """Test Repository: display QA artifacts from db/qa_testing.db."""
    st.title("Test Repository")
    st.caption("View test cases, automation, execution results, and bugs from the QA database.")

    if get_connection is None:
        st.error("Database connection is not available. Ensure db.py exists and SQLite is available.")
        return

    projects = _repo_projects()
    dashboard_names = _load_project_names()
    if not projects:
        projects = [{"project_key": k, "project_name": v} for k, v in dashboard_names.items()] if dashboard_names else [{"project_key": "EC", "project_name": "Excellence Center"}]
    project_options = [p["project_key"] for p in projects]
    project_labels = {p["project_key"]: p["project_name"] for p in projects}

    st.divider()
    col_proj, col_us, col_search = st.columns([1, 1, 2])
    with col_proj:
        project_key = st.selectbox("Project", options=project_options, format_func=lambda k: project_labels.get(k, k), key="repo_project")
    with col_us:
        user_stories = _dedupe_user_stories_by_jira(_repo_user_stories(project_key))
        us_options = [""] + [u["jira_id"] for u in user_stories]
        us_labels = {u["jira_id"]: _format_repo_us_dropdown_label(u["jira_id"], u.get("title")) for u in user_stories}
        us_labels[""] = "All User Stories"
        jira_filter = st.selectbox("User Story", options=us_options, format_func=lambda x: us_labels.get(x, x) if x else "All User Stories", key="repo_us")
    with col_search:
        search = st.text_input("Search test cases", placeholder="Search by testcase_id, title, description…", key="repo_search")

    metrics = _repo_metrics(project_key, jira_filter or None, search or "")
    st.subheader("Project metrics")
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total User Stories", metrics["total_us"])
    m2.metric("Total Test Cases", metrics["total_tc"])
    m3.metric("Automated", metrics["automated"])
    m4.metric("Passed", metrics["passed"])
    m5.metric("Failed", metrics["failed"])

    st.divider()
    st.subheader("Test cases by User Story")

    grouped = _repo_test_cases_grouped(project_key, jira_filter or None, search or "")
    if not grouped:
        st.info("No test cases found. Populate the database from the pipeline (sync test cases), or add data manually.")
        return

    for us in grouped:
        _us_title = (us.get("us_title") or "").strip()
        # Only append title if it's meaningful (not empty and not identical to the Jira ID)
        _us_label = f"**{us['jira_id']}**" if (not _us_title or _us_title == us["jira_id"]) \
                    else f"**{us['jira_id']}** — {_us_title[:60]}{'…' if len(_us_title) > 60 else ''}"
        with st.expander(_us_label, expanded=True):
            rows = us["test_cases"]
            table_data = [
                {
                    "Test Case ID": tc["testcase_id"],
                    "Title": (tc["title"] or "")[:50] + ("…" if len(tc.get("title") or "") > 50 else ""),
                    "Priority": tc["priority"],
                    "Automation": tc["automation_status"],
                    "Last Execution": tc["last_execution_status"],
                    "Last Run": (tc["last_execution_date"] or "—")[:19] if tc.get("last_execution_date") else "—",
                }
                for tc in rows
            ]
            st.dataframe(table_data, width="stretch", hide_index=True)
            for tc in rows:
                with st.expander(f"📋 {tc['testcase_id']}: {(tc.get('title') or '')[:50]}{'…' if len(tc.get('title') or '') > 50 else ''}"):
                    tab_steps, tab_auto, tab_exec, tab_bugs = st.tabs(["Steps", "Automation Script", "Execution Results", "Bugs"])
                    with tab_steps:
                        steps = _repo_steps(tc["testcase_id"])
                        if steps:
                            # st.table() adds an extra 0-based index column; use dataframe with hidden index
                            st.dataframe(
                                [
                                    {
                                        "Step": s["step_number"],
                                        "Action": s["step_action"],
                                        "Expected": s["expected_result"],
                                    }
                                    for s in steps
                                ],
                                width="stretch",
                                hide_index=True,
                            )
                        else:
                            st.caption("No steps recorded.")
                    with tab_auto:
                        auto = _repo_automation(tc["testcase_id"])
                        if auto:
                            for a in auto:
                                st.markdown(f"**{a['script_name']}**")
                                st.text(f"Path: {a['script_path']}")
                                st.caption(f"Framework: {a['framework']} | Created: {a['created_date']}")
                        else:
                            st.caption("No automation script linked.")
                    with tab_exec:
                        execs = _repo_executions(tc["testcase_id"])
                        jid = (tc.get("jira_id") or "").strip()
                        result_txt_path = (
                            os.path.join(PROJECT_ROOT, "ResultReport", f"result_{jid}.txt") if jid else ""
                        )
                        if execs:
                            for ei, e in enumerate(execs):
                                st.markdown(f"**{e['execution_status']}** — {e['execution_date']}")
                                if e.get("report_path"):
                                    st.caption(f"Artifacts (DB): {e['report_path']}")
                                if e.get("execution_logs"):
                                    st.text_area(
                                        "Error / detail (from execution)",
                                        value=e["execution_logs"],
                                        height=120,
                                        disabled=True,
                                        label_visibility="collapsed",
                                        key=f"logs_{tc['testcase_id']}_{ei}",
                                    )
                        else:
                            st.caption("No execution results in the database yet.")
                        if jid and result_txt_path and os.path.isfile(result_txt_path):
                            with st.expander(f"📄 Result summary — result_{jid}.txt (readable)", expanded=False):
                                try:
                                    with open(result_txt_path, "r", encoding="utf-8", errors="replace") as rf:
                                        txt = rf.read()
                                    # text_area + fixed key caches value in session_state; an early empty read
                                    # sticks forever. Key must change when the file changes.
                                    sti = os.stat(result_txt_path)
                                    _result_key = (
                                        f"result_txt_view_{tc['testcase_id']}_{int(sti.st_mtime)}_{sti.st_size}"
                                    )
                                    st.text_area(
                                        "Contents",
                                        value=txt,
                                        height=320,
                                        disabled=True,
                                        label_visibility="collapsed",
                                        key=_result_key,
                                    )
                                except OSError:
                                    st.caption("Could not read the result file.")
                        elif jid and not execs:
                            st.caption("If execution finished, run sync (pipeline success) or open ResultReport on disk.")
                    with tab_bugs:
                        bug_list = _repo_bugs(tc["testcase_id"])
                        if bug_list:
                            for b in bug_list:
                                st.markdown(f"**{b['jira_bug_id']}** — {b['bug_status']}")
                                st.caption(f"Created: {b['created_date']}")
                        else:
                            st.caption("No bugs linked.")


def _render_ishir_page_header() -> None:
    """Render ISHIR branded strip above page content — visual only."""
    if _is_corporate_theme():
        st.markdown(
            """
            <div style="padding:0.35rem 0 1rem 0; margin-bottom:1rem;
                        border-bottom:2px solid #FFD400;">
              <span style="font-size:1.05rem; font-weight:800; color:#111;">
                ISHIR Agentic AI QA Workflow
              </span>
              <span style="font-size:0.88rem; color:#64748B; font-weight:500;">
                &nbsp;·&nbsp;Accelerating Software Quality with AI Agents
              </span>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return
    st.markdown(
        """
        <div style="padding:1.25rem 0 1rem 0; border-bottom:3px solid #FFD400; margin-bottom:1.5rem;">
          <div style="font-size:1.6rem; font-weight:800; color:#111; line-height:1.2;">
            ISHIR Agentic AI QA Workflow
          </div>
          <div style="font-size:0.85rem; color:#555; margin-top:4px;">
            Accelerating Software Quality with AI Agents
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_ishir_footer() -> None:
    """Render ISHIR branded footer — visual only."""
    st.markdown(
        """
        <div style="margin-top:3rem; padding:1rem 0 0.5rem 0;
                    border-top:1px solid #E0E0E0; text-align:center;">
          <span style="font-size:0.78rem; color:#777;">
            Powered by&nbsp;
            <strong style="color:#111;">iSHIR</strong>
            &nbsp;AI Innovation Lab &nbsp;|&nbsp;
            <span style="color:#FFD400;">&#9632;</span>
            &nbsp;ISHIR Agentic AI QA Workflow
          </span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def main():
    global _pipeline_worker_ticket, _pipeline_worker_result

    # show_sidebar_navigation: Streamlit ≥1.36 — hides auto "app" / pages links (see .streamlit/config.toml too)
    try:
        st.set_page_config(
            page_title="ISHIR Agentic AI QA Workflow",
            page_icon="🤖",
            layout="wide",
            initial_sidebar_state="expanded",
            show_sidebar_navigation=False,
        )
    except TypeError:
        st.set_page_config(
            page_title="ISHIR Agentic AI QA Workflow",
            page_icon="🤖",
            layout="wide",
            initial_sidebar_state="expanded",
        )

    # ----- Sidebar: logo top, then menu, then profile + Logout -----
    if _is_corporate_theme():
        st.sidebar.markdown(
            """
            <div style="padding: 0.5rem 0 0.25rem 0;">
              <div style="line-height:1;">
                <span style="font-size:1.5rem; font-weight:900; color:#F8FAFC; letter-spacing:-1px;">
                  <span style="position:relative; display:inline-block;">
                    i<span style="position:absolute; top:-4px; left:50%; transform:translateX(-50%);
                           width:6px; height:6px; background:#FFD400; border-radius:50%;
                           display:block;"></span>
                  </span>SHIR
                </span>
              </div>
              <div style="font-size:0.72rem; color:#94A3B8; margin-top:2px; letter-spacing:0.5px;">
                26 Years of Delivering Innovation
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.sidebar.markdown(
            """
            <div style="padding: 0.5rem 0 0.25rem 0;">
              <div style="line-height:1;">
                <span style="font-size:1.5rem; font-weight:900; color:#111; letter-spacing:-1px;">
                  <span style="position:relative; display:inline-block;">
                    i<span style="position:absolute; top:-4px; left:50%; transform:translateX(-50%);
                           width:6px; height:6px; background:#FFD400; border-radius:50%;
                           display:block;"></span>
                  </span>SHIR
                </span>
              </div>
              <div style="font-size:0.72rem; color:#555; margin-top:2px; letter-spacing:0.5px;">
                26 Years of Delivering Innovation
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    st.sidebar.markdown("---")
    _nav = _nav_options_for_role(st.session_state.get("user_role"))
    if _is_corporate_theme():
        st.sidebar.markdown(
            '<p style="font-size:0.65rem;letter-spacing:0.14em;color:#94A3B8;'
            'text-transform:uppercase;margin:0 0 0.4rem 0;font-weight:600;">Menu</p>',
            unsafe_allow_html=True,
        )
    else:
        st.sidebar.markdown("**Menu**")
    page = st.sidebar.radio(
        "Go to",
        _nav,
        label_visibility="collapsed",
    )
    st.sidebar.markdown("---")
    _fn = st.session_state.get("full_name", "") or ""
    _role = st.session_state.get("user_role", "") or ""
    if _is_corporate_theme():
        _ini = html.escape(_profile_initials(_fn))
        st.sidebar.markdown(
            f"""
            <div style="display:flex;align-items:center;gap:12px;margin:4px 0 10px 0;">
              <div style="min-width:42px;width:42px;height:42px;border-radius:50%;
                   background:linear-gradient(145deg,#FFD400,#E6AC00);
                   display:flex;align-items:center;justify-content:center;
                   font-weight:800;color:#111;font-size:0.82rem;letter-spacing:0.02em;">
                {_ini}
              </div>
              <div style="line-height:1.35;">
                <div style="font-weight:600;color:#F1F5F9;font-size:0.95rem;">
                  {html.escape(_fn)}
                </div>
                <div style="font-size:0.8rem;color:#94A3B8;">Role: {html.escape(_role)}</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.sidebar.write(f"👤 {_fn}")
        st.sidebar.write(f"Role: {_role}")
    if st.sidebar.button("Logout"):
        try:
            from session_cookie import clear_login_cookie

            clear_login_cookie()
        except ImportError:
            pass
        st.session_state.clear()
        st.switch_page("pages/_Login.py")

    if page == "Dashboard":
        _inject_ishir_css()
        _render_ishir_page_header()
        _render_dashboard()
        _render_ishir_footer()
        return
    if page == "Test Repository":
        _inject_ishir_css()
        _render_ishir_page_header()
        _render_test_repository()
        _render_ishir_footer()
        return

    # ----- Pipeline run state (for STOP and background run) -----
    if "pipeline_running" not in st.session_state:
        st.session_state["pipeline_running"] = False
    if "pipeline_done" not in st.session_state:
        st.session_state["pipeline_done"] = False
    if "pipeline_stop_requested" not in st.session_state:
        st.session_state["pipeline_stop_requested"] = False
    if "pipeline_process" not in st.session_state:
        st.session_state["pipeline_process"] = None
    if "pipeline_current_ticket" not in st.session_state:
        st.session_state["pipeline_current_ticket"] = None
    if "pipeline_result" not in st.session_state:
        st.session_state["pipeline_result"] = None
    if "pipeline_thread" not in st.session_state:
        st.session_state["pipeline_thread"] = None
    if "pipeline_step" not in st.session_state:
        st.session_state["pipeline_step"] = "full"
    if "pipeline_ticket_list" not in st.session_state:
        st.session_state["pipeline_ticket_list"] = []

    if st.session_state["pipeline_running"]:
        with _pipeline_worker_lock:
            ct = _pipeline_worker_ticket
        if ct:
            st.session_state["pipeline_current_ticket"] = ct
        st.session_state["pipeline_process"] = _current_process

    # ----- STOP button when pipeline is running -----
    if st.session_state["pipeline_running"]:
        current_ticket  = st.session_state.get("pipeline_current_ticket") or "…"
        current_step    = st.session_state.get("pipeline_step", "full")
        step_label      = STEP_LABELS.get(current_step, current_step)
        all_tickets     = st.session_state.get("pipeline_ticket_list", [])
        tickets_display = ", ".join(all_tickets) if all_tickets else current_ticket
        st.sidebar.markdown("---")
        st.sidebar.markdown(
            f"""
            <div style="background:#FFD400; color:#111; font-weight:700;
                        border-radius:6px; padding:8px 10px; font-size:0.9rem;">
              ⚙ {step_label}
            </div>
            <div style="font-size:0.8rem; color:#333; margin-top:4px;">
              Ticket(s): <strong>{tickets_display}</strong>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.sidebar.button("⏹ STOP PIPELINE", type="secondary", use_container_width=True, key="sidebar_stop_btn"):
            # 1. Signal the worker thread (thread-safe Event — no ScriptRunContext needed)
            _stop_event.set()
            st.session_state["pipeline_stop_requested"] = True
            # 2. Kill the OS process group immediately so agents stop now
            proc = _current_process or st.session_state.get("pipeline_process")
            if proc is not None and proc.poll() is None:
                _kill_process(proc)
            # 3. Set final state HERE in the main thread so the UI updates instantly
            #    after st.rerun() — don't wait for the background thread to do it
            st.session_state["pipeline_running"] = False
            st.session_state["pipeline_done"] = True
            st.session_state["pipeline_result"] = {
                "batch_progress": [],
                "last_stdout": "Pipeline stopped by user.",
                "last_stderr": "",
                "last_returncode": -1,
                "ticket_list": st.session_state.get("pipeline_ticket_list", []),
                "batches": [],
                "step": st.session_state.get("pipeline_step", "full"),
                "project_key": "",
            }
            st.rerun()
        st.sidebar.markdown("---")

    # ----- Run pipeline page (core functionality: keep intact) -----
    # Preserve: Project, Run for Current Sprint, Jira Ticket ID, 4 step buttons,
    # RUN FULL PIPELINE, STOP in sidebar, background worker, result display.
    _inject_ishir_css()
    # ── Brand header: logo left | title + subtitle right ───────────────────
    _render_ishir_page_header()

    if st.session_state["pipeline_running"]:
        _step        = st.session_state.get("pipeline_step", "full")
        _step_label  = STEP_LABELS.get(_step, _step)
        _all_tickets = st.session_state.get("pipeline_ticket_list", [])
        _tickets_str = ", ".join(_all_tickets) if _all_tickets else (st.session_state.get("pipeline_current_ticket") or "…")
        st.markdown(
            f"""
            <div style="background:#FFD400; color:#111; font-weight:700; font-size:1rem;
                        border-radius:8px; padding:14px 18px; margin-bottom:0.5rem;
                        display:flex; align-items:center; gap:10px;">
              <span style="font-size:1.3rem;">⚙</span>
              <span>{_step_label} running…&nbsp;&nbsp;|&nbsp;&nbsp;Ticket(s): {_tickets_str}</span>
            </div>
            <div style="font-size:0.82rem; color:#555; margin-bottom:1rem;">
              Use <strong>⏹ STOP PIPELINE</strong> in the sidebar to cancel at any time.
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown("---")
        # Auto-poll: check every 2s whether the background thread has finished.
        # The thread cannot call st.rerun() itself, so we poll here and rerun
        # as soon as the thread exits — this clears the "running" banner automatically.
        _bg_thread = st.session_state.get("pipeline_thread")
        if _bg_thread is not None and not _bg_thread.is_alive():
            _sync_pipeline_worker_result_to_session()
            st.session_state["pipeline_running"] = False
            st.session_state["pipeline_process"] = None
            st.session_state["pipeline_current_ticket"] = None
            st.session_state["pipeline_thread"] = None
            st.rerun()
        else:
            time.sleep(2)
            st.rerun()

    # ----- Simple help -----
    with st.expander("📖 How to use (quick guide)", expanded=False):
        st.markdown("""
        **1.** Select **Project**, then either check **Run for Current Sprint** or enter a **Jira Ticket ID** (e.g. EC-298).  
        **2.** Click **RUN FULL PIPELINE** or a step button.  
        **3.** Wait; use **⏹ STOP PIPELINE** in the sidebar to cancel.  
        **4.** When you see "Pipeline run finished", you're done.  
        """)
    st.divider()

    # ----- Input: Project, Run for Current Sprint, Ticket ID -----
    project_names = _load_project_names()
    project_labels = project_names if project_names else PROJECT_OPTIONS
    project_options = list(project_labels.keys())

    proj_col, _ = st.columns([1, 3])
    with proj_col:
        project_key = st.selectbox(
            "**Project**",
            options=project_options,
            format_func=lambda k: project_labels.get(k, k),
            index=0,
            help="Select the Jira project.",
        )
    run_for_sprint = st.checkbox("Run for Current Sprint", value=False, help="Fetch User Stories from the active sprint for the selected project.")
    jira_ticket_id = ""
    if not run_for_sprint:
        jira_ticket_id = st.text_input(
            "**Jira Ticket ID**",
            value="",
            placeholder="e.g. EC-298",
            help="Enter a single Jira ticket ID.",
        ).strip()

    # Resolve ticket list
    ticket_list: list[str] = []
    if run_for_sprint:
        if get_sprint_story_keys is None:
            st.error("Jira fetch is not available. Ensure jira_fetch.py is in the project root and dependencies are installed.")
            st.stop()
        with st.spinner("Fetching User Stories from active sprint…"):
            ticket_list, fetch_error = get_sprint_story_keys(project_key)
        if fetch_error:
            st.error(f"Could not fetch Jira issues: {fetch_error}")
            st.stop()
        if not ticket_list:
            st.warning("No issues found for this project.")
            st.stop()
        st.caption(f"Found {len(ticket_list)} issue(s): {', '.join(ticket_list[:10])}{'…' if len(ticket_list) > 10 else ''}")
    else:
        if jira_ticket_id:
            ticket_list = [jira_ticket_id]
    if not ticket_list:
        st.info("Enter a **Jira Ticket ID** or check **Run for Current Sprint**. Then click **RUN FULL PIPELINE**.")

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

    # Validate ticket list when user clicks run or a step button
    if (run_full or step_clicked) and not ticket_list:
        st.error("Enter a **Jira Ticket ID** or check **Run for Current Sprint** to run the pipeline.")
        st.stop()

    tid = ticket_list[0] if ticket_list else ""
    if step_clicked and not run_full and tid:
        if btn_automation and not _test_cases_exist(tid):
            tc_path = os.path.join(FOLDERS["test_cases"], f"{tid}_Testcase.txt")
            st.error(
                f"⛔ **File not found:** `{os.path.relpath(tc_path, PROJECT_ROOT)}`\n\n"
                "Test cases must be generated before automation scripts can be written. "
                "Please run **Generate Test Cases** first."
            )
            st.stop()
        if btn_execute:
            script_path = os.path.join(FOLDERS["scripts"], f"Script_{tid}.spec.ts")
            if not os.path.isfile(script_path):
                st.error(
                    f"⛔ **File not found:** `{os.path.relpath(script_path, PROJECT_ROOT)}`\n\n"
                    "Automation scripts must be generated before execution can run. "
                    "Please run **Generate Automation Scripts** first."
                )
                st.stop()
        if btn_bugs:
            result_path = os.path.join(FOLDERS["results"], f"execution_{tid}.json")
            if not os.path.isfile(result_path):
                st.error(
                    f"⛔ **File not found:** `{os.path.relpath(result_path, PROJECT_ROOT)}`\n\n"
                    "Execution results must exist before bugs can be created. "
                    "Please run **Execute Automation** first."
                )
                st.stop()

    if (run_full or step_clicked) and not st.session_state["pipeline_running"]:
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
        st.session_state["pipeline_stop_requested"] = False
        st.session_state["pipeline_done"] = False
        st.session_state["pipeline_result"] = None
        st.session_state["pipeline_running"] = True
        st.session_state["pipeline_step"] = step
        st.session_state["pipeline_ticket_list"] = ticket_list
        _stop_event.clear()   # reset stop signal for fresh run
        with _pipeline_worker_lock:
            _pipeline_worker_ticket = None
            _pipeline_worker_result = None
        batches = [ticket_list[i : i + BATCH_SIZE] for i in range(0, len(ticket_list), BATCH_SIZE)]
        thread = threading.Thread(
            target=_pipeline_worker,
            args=(ticket_list, step, batches, project_key),
            daemon=True,
        )
        thread.start()
        st.session_state["pipeline_thread"] = thread
        st.rerun()

    if st.session_state.get("pipeline_done") and st.session_state.get("pipeline_result"):
        res = st.session_state["pipeline_result"]
        ticket_list_res = res["ticket_list"]
        batch_progress = res["batch_progress"]
        last_stdout = res["last_stdout"]
        last_stderr = res["last_stderr"]
        last_returncode = res["last_returncode"]
        ticket_to_show = ticket_list_res[0] if ticket_list_res else ""

        was_stopped = st.session_state.get("pipeline_stop_requested") or last_stdout == "Pipeline stopped by user."
        if was_stopped:
            st.markdown(
                """
                <div style="background:#FFD400; color:#111; font-weight:700; font-size:1rem;
                            border-radius:8px; padding:14px 18px; margin-bottom:0.5rem;">
                  ⏹ Pipeline stopped by user.
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.session_state["pipeline_stop_requested"] = False   # reset for next run
        elif last_returncode != 0:
            st.warning(f"Pipeline failed (exit code {last_returncode}).")
        else:
            st.success("Pipeline run finished.")

        if batch_progress:
            st.subheader("Batch progress")
            for line in batch_progress:
                st.text(line)

        st.subheader("Execution Results")
        result_txt = get_final_result_content(ticket_to_show)
        summary = get_execution_summary(ticket_to_show)
        saved = list_saved_files(ticket_to_show)

        # Generated Test Cases
        tc_path = os.path.join(FOLDERS["test_cases"], f"{ticket_to_show}_Testcase.txt")
        with st.expander("Generated Test Cases"):
            if os.path.isfile(tc_path):
                try:
                    with open(tc_path, "r", encoding="utf-8", errors="replace") as f:
                        st.text(f.read())
                except Exception:
                    st.caption("Could not read file.")
            else:
                st.caption("No test case file found for this ticket.")

        # Automation Scripts
        script_path = os.path.join(FOLDERS["scripts"], f"Script_{ticket_to_show}.spec.ts")
        with st.expander("Automation Scripts"):
            if os.path.isfile(script_path):
                try:
                    with open(script_path, "r", encoding="utf-8", errors="replace") as f:
                        st.code(f.read(), language="typescript")
                except Exception:
                    st.caption("Could not read file.")
            else:
                st.caption("No automation script found for this ticket.")

        # Execution Report
        result_path_pipe = os.path.join(FOLDERS["results"], f"result_{ticket_to_show}.txt")
        with st.expander("Execution Report"):
            if result_txt:
                rp_key = "exec_report_txt"
                if os.path.isfile(result_path_pipe):
                    st_rp = os.stat(result_path_pipe)
                    rp_key = f"exec_report_{ticket_to_show}_{int(st_rp.st_mtime)}_{st_rp.st_size}"
                st.text_area(
                    "Execution result",
                    value=result_txt,
                    height=200,
                    disabled=True,
                    label_visibility="collapsed",
                    key=rp_key,
                )
            if summary:
                st.caption(summary)
            if not result_txt and not summary:
                st.info("Result file may not be created yet (e.g. if execution step did not run).")

        # Created Bugs
        res_dir = FOLDERS["results"]
        bug_files = [n for n in (os.listdir(res_dir) if os.path.isdir(res_dir) else []) if n.startswith(f"bug_{ticket_to_show}")]
        with st.expander("Created Bugs"):
            if bug_files:
                for name in sorted(bug_files):
                    path = os.path.join(res_dir, name)
                    st.text(f"• {os.path.relpath(path, PROJECT_ROOT)}")
                    try:
                        with open(path, "r", encoding="utf-8", errors="replace") as f:
                            st.code(f.read(), language="text")
                    except Exception:
                        pass
            else:
                st.caption("No bug files found for this ticket.")

        st.subheader("Saved files (in AgenticAIAutogen)")
        if saved:
            for label, path in saved:
                rel = os.path.relpath(path, PROJECT_ROOT)
                st.text(f"• {label}: {rel}")
            st.caption(f"All files are saved under: {PROJECT_ROOT}")
        else:
            st.caption("No result files found yet for this Jira ID. They appear after the pipeline runs.")

        full_log = (last_stdout or "") + ("\n\n--- stderr ---\n" + last_stderr if last_stderr else "")
        if full_log:
            with st.expander("View full execution log (for debugging)"):
                st.code(full_log, language="text")

        st.session_state["pipeline_done"] = False
        st.session_state["pipeline_result"] = None
    elif st.session_state.get("pipeline_running"):
        st.subheader("Execution Results")
        st.caption("Pipeline is running. Use **⏹ STOP PIPELINE** in the sidebar to cancel.")
    else:
        st.subheader("Execution Results")
        st.info("Select **Project**, then **Jira Ticket ID** or **Run for Current Sprint**, and click **RUN FULL PIPELINE**. Use **⏹ STOP PIPELINE** in the sidebar to cancel a run.")

    _render_ishir_footer()


if __name__ == "__main__":
    main()

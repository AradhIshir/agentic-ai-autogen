"""
Login page for ISHIR Agentic AI QA Workflow.
Database: db/qa_testing.db, table users (admin adds users; no signup).
"""
import os
import sqlite3
import sys
from datetime import datetime, timezone

import bcrypt
import streamlit as st

_PAGES_DIR = os.path.dirname(os.path.abspath(__file__))
_UI_DIR = os.path.abspath(os.path.join(_PAGES_DIR, ".."))
_PROJECT_ROOT = os.path.abspath(os.path.join(_UI_DIR, ".."))
DB_PATH = os.path.join(_PROJECT_ROOT, "db", "qa_testing.db")

for _p in (_PROJECT_ROOT, _UI_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_LOGO_CANDIDATES = (
    os.path.join(_UI_DIR, "ishir_logo.png"),
    os.path.join(_UI_DIR, "assets", "ishir_logo.png"),
)

st.set_page_config(
    page_title="Login — ISHIR Agentic AI QA Workflow",
    page_icon="🤖",
    layout="centered",
    initial_sidebar_state="collapsed",
)

try:
    from session_cookie import prime_cookies_and_maybe_restore, save_login_cookie

    prime_cookies_and_maybe_restore()
except ImportError:
    def save_login_cookie(_email: str) -> None:  # type: ignore
        return

if st.session_state.get("logged_in"):
    st.switch_page("app.py")
    st.stop()

st.markdown(
    """
    <style>
    html, body, .stApp {
        background-color: #111111 !important;
        color: #FFFFFF !important;
    }
    section[data-testid="stSidebar"] { display: none !important; }
    [data-testid="stSidebarCollapsedControl"] { display: none !important; }
    header[data-testid="stHeader"] { background-color: #111111 !important; }
    .main .block-container {
        padding-top: 2rem !important;
        max-width: 420px !important;
        background-color: #1A1A1A !important;
        border: 1px solid #2A2A2A !important;
        border-radius: 12px !important;
        box-shadow: 0 8px 32px rgba(0,0,0,0.4) !important;
    }
    .login-subtitle {
        color: #9A9A9A !important;
        font-size: 0.95rem;
        margin-top: 0.35rem;
    }
    label, .stTextInput label {
        color: #9A9A9A !important;
    }
    .stTextInput input {
        background-color: #1A1A1A !important;
        color: #FFFFFF !important;
        border: 1px solid #2A2A2A !important;
        border-radius: 8px !important;
    }
    .stTextInput input:focus {
        border-color: #F5C518 !important;
        box-shadow: 0 0 0 2px rgba(245, 197, 24, 0.25) !important;
    }
    .stFormSubmitButton > button {
        background: #F5C518 !important;
        color: #000000 !important;
        font-weight: 700 !important;
        border: none !important;
        border-radius: 8px !important;
        width: 100% !important;
    }
    .stFormSubmitButton > button:hover {
        background: #FFDA44 !important;
        color: #000000 !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

logo_path = next((p for p in _LOGO_CANDIDATES if os.path.isfile(p)), None)

col_outer_l, col_center, col_outer_r = st.columns([1, 2, 1])
with col_center:
    if logo_path:
        c1, c2, c3 = st.columns([1, 2, 1])
        with c2:
            st.image(logo_path, use_container_width=True)
    else:
        st.markdown(
            """
            <div style="text-align:center; padding: 0.25rem 0 0.5rem 0;">
              <div style="line-height:1;">
                <span style="font-size:1.75rem; font-weight:900;
                             color:#FFFFFF; letter-spacing:-1px;">
                  <span style="position:relative; display:inline-block;">
                    i<span style="position:absolute; top:-5px; left:50%;
                           transform:translateX(-50%); width:7px; height:7px;
                           background:#F5C518; border-radius:50%;
                           display:block;"></span>
                  </span>SHIR
                </span>
              </div>
              <div style="font-size:0.72rem; color:#9A9A9A;
                          margin-top:4px; letter-spacing:0.5px;">
                26 Years of Delivering Innovation
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown(
        '<p class="login-subtitle" style="text-align:center;">'
        'ISHIR Agentic AI QA Workflow</p>',
        unsafe_allow_html=True,
    )

    with st.form("login_form", clear_on_submit=False):
        email    = st.text_input("Email",
                                 placeholder="you@company.com")
        password = st.text_input("Password",
                                 type="password",
                                 placeholder="••••••••")
        submitted = st.form_submit_button("Login")

        if submitted:
            email_clean  = (email or "").strip()
            password_val = password or ""

            if not email_clean or not password_val:
                st.error("Please enter both email and password.")
                st.stop()

            if not os.path.isfile(DB_PATH):
                st.error("Database not found. Contact admin.")
                st.stop()

            try:
                conn = sqlite3.connect(DB_PATH)
                conn.row_factory = sqlite3.Row
                # Case-insensitive email (SQLite = is case-sensitive for ASCII)
                row = conn.execute(
                    "SELECT id, full_name, email, password_hash, role, is_active "
                    "FROM users WHERE LOWER(TRIM(email)) = LOWER(TRIM(?)) "
                    "LIMIT 1",
                    (email_clean,),
                ).fetchone()
                conn.close()
            except sqlite3.Error:
                st.error("Could not connect to database. Contact admin.")
                st.stop()

            if row is None:
                st.error("Invalid email or password.")
                st.stop()

            stored_hash = row["password_hash"]
            if stored_hash is None:
                st.error("Invalid email or password.")
                st.stop()

            # Trim whitespace/newlines so pasted hashes still verify
            if isinstance(stored_hash, bytes):
                stored_bytes = stored_hash.strip()
            else:
                stored_bytes = str(stored_hash).strip().encode("utf-8")

            try:
                ok = bcrypt.checkpw(
                    password_val.encode("utf-8"),
                    stored_bytes
                )
            except (ValueError, TypeError):
                ok = False

            if not ok:
                st.error("Invalid email or password.")
                st.stop()

            if row["is_active"] is not None and int(row["is_active"]) == 0:
                st.error("Your account is deactivated. Contact admin.")
                st.stop()

            now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            try:
                conn2 = sqlite3.connect(DB_PATH)
                conn2.execute(
                    "UPDATE users SET last_login_at = ? WHERE id = ?",
                    (now_utc, row["id"])
                )
                conn2.commit()
                conn2.close()
            except sqlite3.Error:
                pass

            st.session_state["logged_in"] = True
            st.session_state["user_email"] = row["email"]
            st.session_state["user_role"] = (row["role"] or "").strip().lower()
            st.session_state["full_name"] = row["full_name"] or ""
            try:
                save_login_cookie(row["email"])
            except Exception:
                pass
            st.switch_page("app.py")
            st.stop()
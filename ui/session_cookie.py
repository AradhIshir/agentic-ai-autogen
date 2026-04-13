"""
Persistent login across browser refresh using a signed cookie + extra_streamlit_components.

Set QA_SESSION_SECRET in .env for production (defaults to dev placeholder).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import sqlite3
from datetime import datetime, timedelta, timezone

_UI_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_UI_DIR, ".."))
DB_PATH = os.path.join(_PROJECT_ROOT, "db", "qa_testing.db")

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))
except Exception:
    pass

import extra_streamlit_components as stx
import streamlit as st

COOKIE_NAME = "ishir_qa_auth"
COOKIE_DAYS = 14

# Single CookieManager per browser session. Do NOT use @st.cache_resource — Streamlit forbids
# widgets inside cached functions (CachedWidgetWarning) and caching created duplicate
# internal keys like 'get_all' (StreamlitDuplicateElementKey).
_SESSION_STX_CM_KEY = "_ishir_stx_cookie_manager"


def _cookie_manager():
    if _SESSION_STX_CM_KEY not in st.session_state:
        st.session_state[_SESSION_STX_CM_KEY] = stx.CookieManager(key="ishir_qa_cookie_mgr")
    return st.session_state[_SESSION_STX_CM_KEY]


def _secret() -> bytes:
    s = (os.environ.get("QA_SESSION_SECRET") or "").strip() or "dev-only-set-QA_SESSION_SECRET-in-env"
    return s.encode("utf-8")


def _sign(email: str) -> str:
    em = email.strip().lower()
    return hmac.new(_secret(), em.encode("utf-8"), hashlib.sha256).hexdigest()


def _pack_token(email: str) -> str:
    em = email.strip().lower()
    raw = f"{em}:{_sign(email)}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _unpack_token(token: str) -> str | None:
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        email, sig = raw.rsplit(":", 1)
        email = email.strip().lower()
        if not email:
            return None
        if hmac.compare_digest(_sign(email), sig):
            return email
    except (ValueError, OSError, UnicodeError):
        pass
    return None


def _load_user_row(email_lower: str) -> sqlite3.Row | None:
    if not os.path.isfile(DB_PATH):
        return None
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, full_name, email, role, is_active FROM users "
            "WHERE LOWER(TRIM(email)) = ? LIMIT 1",
            (email_lower,),
        ).fetchone()
        conn.close()
        return row
    except sqlite3.Error:
        return None


def prime_cookies_and_maybe_restore() -> None:
    """
    CookieManager needs one render before get_all() returns browser cookies.
    After refresh: first run primes + rerun; second run reads cookie and restores session_state.

    IMPORTANT: extra_streamlit_components CookieManager.get_all() uses Streamlit key "get_all" by
    default. Calling get_all() twice in the same script run causes StreamlitDuplicateElementKey.
    """
    if st.session_state.get("logged_in"):
        return

    mgr = _cookie_manager()

    if not st.session_state.get("_ishir_cookie_primmed"):
        _ = mgr.get_all(key="ishir_cm_getall_prime")
        st.session_state["_ishir_cookie_primmed"] = True
        st.rerun()

    all_c = mgr.get_all(key="ishir_cm_getall_restore")
    if not all_c:
        return
    raw = all_c.get(COOKIE_NAME)
    if not raw:
        return
    email_lower = _unpack_token(raw)
    if not email_lower:
        mgr.delete(COOKIE_NAME)
        return
    row = _load_user_row(email_lower)
    if row is None:
        mgr.delete(COOKIE_NAME)
        return
    if row["is_active"] is not None and int(row["is_active"]) == 0:
        mgr.delete(COOKIE_NAME)
        return

    st.session_state["logged_in"] = True
    st.session_state["user_email"] = row["email"]
    st.session_state["user_role"] = (row["role"] or "").strip().lower()
    st.session_state["full_name"] = row["full_name"] or ""


def save_login_cookie(email: str) -> None:
    mgr = _cookie_manager()
    token = _pack_token(email)
    exp = datetime.now(timezone.utc) + timedelta(days=COOKIE_DAYS)
    mgr.set(COOKIE_NAME, token, expires_at=exp)


def clear_login_cookie() -> None:
    try:
        _cookie_manager().delete(COOKIE_NAME)
    except Exception:
        pass

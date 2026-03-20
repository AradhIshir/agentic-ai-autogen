"""
SQLite connection helper for the QA pipeline.
Default: db/qa_testing.db under the project root (AgenticAIAutogen).
Override with QA_DB_PATH in .env if needed.
"""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

# Load .env so QA_DB_PATH is available
try:
    from dotenv import load_dotenv
    _root = Path(__file__).resolve().parent
    load_dotenv(_root / ".env")
except Exception:
    pass

# Project root = directory containing this file
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = PROJECT_ROOT / "db" / "qa_testing.db"


def get_db_path() -> str:
    """Path to the SQLite database file (default: db/qa_testing.db)."""
    path = os.environ.get("QA_DB_PATH", "").strip()
    if path:
        return path
    return str(DEFAULT_DB_PATH)


def _ensure_unique_indexes(conn: sqlite3.Connection) -> None:
    """Idempotent: create UNIQUE indexes that prevent duplicate rows.
    Called once per connection; SQLite's IF NOT EXISTS makes it a no-op when they already exist.
    """
    conn.executescript("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_user_stories_project_jira
            ON user_stories(project_key, jira_id);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_projects_key
            ON projects(project_key);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_test_cases_jira_tc
            ON test_cases(jira_id, testcase_id);
    """)


@contextmanager
def get_connection():
    """Context manager: yields a SQLite connection (auto-commits on exit, closes on exit)."""
    path = get_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row  # access columns by name
    try:
        _ensure_unique_indexes(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_cursor():
    """Yield a connection and cursor for one-off use. Connection is closed after the block."""
    with get_connection() as conn:
        yield conn.cursor()

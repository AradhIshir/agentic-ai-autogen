"""
Jira API helper: fetch issue keys from the active sprint or project.
Uses Jira REST API with basic auth (email + API token).
Expects env: JIRA_USERNAME (or ATLASSIAN_EMAIL), JIRA_API_TOKEN (or ATLASSIAN_API_TOKEN), JIRA_BASE_URL.

JQL uses: project name (e.g. "Excellence Center" for EC), status = "In Progress", issuetype = "Story".
Override project in JQL via JIRA_PROJECT_<KEY> in .env (e.g. JIRA_PROJECT_EC=Excellence Center).
"""

import os
from typing import Optional

import requests

# Load .env from project root (same directory as this file) so env vars are set when imported from Streamlit
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    load_dotenv(_env_path)
except Exception:
    pass


def _get_base_url() -> Optional[str]:
    url = os.environ.get("JIRA_BASE_URL") or os.environ.get("ATLASSIAN_BASE_URL")
    if url:
        return url.rstrip("/")
    domain = os.environ.get("JIRA_DOMAIN")
    if domain:
        d = domain.strip().replace("https://", "").split("/")[0].split(".")[0]
        return f"https://{d}.atlassian.net"
    return None


def _get_auth() -> tuple[str, str]:
    email = os.environ.get("JIRA_USERNAME") or os.environ.get("ATLASSIAN_EMAIL", "")
    token = os.environ.get("JIRA_API_TOKEN") or os.environ.get("ATLASSIAN_API_TOKEN", "")
    return (email, token)


def _fetch_stories(base_url: str, auth: tuple[str, str], jql: str, max_results: int) -> tuple[list[str], str]:
    """Run Jira search with given JQL. Returns (list of issue keys, error_message).
    Uses /rest/api/3/search/jql (old /rest/api/3/search was removed, returns 410)."""
    url = f"{base_url}/rest/api/3/search/jql"
    params = {"jql": jql, "maxResults": max_results, "fields": "key"}
    headers = {"Accept": "application/json"}
    try:
        resp = requests.get(url, params=params, auth=auth, headers=headers, timeout=30)
        if resp.status_code != 200:
            body = resp.text[:500] if resp.text else resp.reason
            return [], f"Jira API returned {resp.status_code}: {body}"
        data = resp.json()
        issues = data.get("issues") or []
        keys = [str(issue.get("key", "")).strip() for issue in issues if issue.get("key")]
        return keys, ""
    except requests.exceptions.RequestException as e:
        err = str(e)
        if hasattr(e, "response") and e.response is not None and e.response.text:
            err = f"{e.response.status_code}: {e.response.text[:300]}"
        return [], f"Jira request failed: {err}"
    except Exception as e:
        return [], f"Error: {type(e).__name__}: {e}"


def _in_progress_status() -> str:
    """Status name for 'In Progress' (Kanban). Override via JIRA_IN_PROGRESS_STATUS if needed."""
    return (os.environ.get("JIRA_IN_PROGRESS_STATUS") or "In Progress").strip() or "In Progress"


# Project key -> value used in JQL (project name or key). Override with JIRA_PROJECT_<KEY> in .env.
_PROJECT_JQL_DEFAULTS = {"EC": "Excellence Center"}


def _project_for_jql(project_key: str) -> str:
    """Return the project value for JQL (name or key). Env JIRA_PROJECT_<KEY> overrides."""
    env_val = os.environ.get(f"JIRA_PROJECT_{project_key}")
    if env_val:
        return env_val.strip()
    return _PROJECT_JQL_DEFAULTS.get(project_key, project_key)


def get_sprint_story_keys(project_key: str, max_results: int = 100) -> tuple[list[str], str]:
    """
    Fetch issue keys for the given project.
    - Scrum: project + sprint in openSprints(), only issues In Progress (backlog in sprint excluded).
    - Kanban (no open sprint): only issues with status "In Progress"; Backlog, Done, and all other
      statuses are excluded.
    Returns (list of keys, error_message). If error_message is non-empty, keys may be empty.
    """
    base_url = _get_base_url()
    if not base_url:
        return [], "JIRA_BASE_URL (or ATLASSIAN_BASE_URL) is not set in .env"
    email, token = _get_auth()
    if not email or not token:
        return [], "JIRA_USERNAME and JIRA_API_TOKEN (or ATLASSIAN_EMAIL / ATLASSIAN_API_TOKEN) must be set in .env"

    auth = (email, token)
    in_progress = _in_progress_status()
    project_jql = _project_for_jql(project_key)
    story_filter = 'issuetype = "Story"'

    # 1) Scrum: active sprint, only In Progress Stories (exclude backlog in sprint)
    jql_sprint = f'project = "{project_jql}" AND sprint in openSprints() AND status = "{in_progress}" AND {story_filter}'
    keys, err = _fetch_stories(base_url, auth, jql_sprint, max_results)
    if err:
        return [], err

    # 2) Kanban: only Stories with status "In Progress" — exclude Backlog, Done, and other types
    if not keys:
        jql_kanban = f'project = "{project_jql}" AND status = "{in_progress}" AND {story_filter} ORDER BY updated DESC'
        keys, err = _fetch_stories(base_url, auth, jql_kanban, max_results)
        if err:
            return [], err

    return keys, ""

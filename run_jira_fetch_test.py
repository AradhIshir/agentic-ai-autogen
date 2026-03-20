#!/usr/bin/env python3
"""
Manual test for Jira fetch. Run from project root:
  python run_jira_fetch_test.py
  or: python run_jira_fetch_test.py EC
  or: python run_jira_fetch_test.py EC --debug   # print key -> status for first 20
Uses .env and prints fetched issue keys or the actual error.
"""
import os
import sys

from dotenv import load_dotenv
load_dotenv()

# Ensure project root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jira_fetch import get_sprint_story_keys, _get_base_url, _get_auth


def debug_jql(project: str) -> None:
    """Print which JQL branch runs and how many keys."""
    import requests
    base = _get_base_url()
    email, token = _get_auth()
    if not base or not email or not token:
        print("Missing Jira env"); return
    auth = (email, token)
    url = f"{base}/rest/api/3/search/jql"
    def run(jql: str) -> list:
        r = requests.get(url, params={"jql": jql, "maxResults": 100, "fields": "key"}, auth=auth, headers={"Accept": "application/json"}, timeout=30)
        if r.status_code != 200:
            print("Error:", r.status_code); return []
        return [i.get("key") for i in (r.json().get("issues") or []) if i.get("key")]
    jql_sprint = f'project = "{project}" AND sprint in openSprints() AND status = "In Progress"'
    jql_fallback = f'project = "{project}" AND status = "In Progress" ORDER BY updated DESC'
    keys_sprint = run(jql_sprint)
    keys_fallback = run(jql_fallback)
    print("Sprint JQL (with status = In Progress):", len(keys_sprint), "->", keys_sprint[:5], "..." if len(keys_sprint) > 5 else "")
    print("Fallback JQL (status = In Progress only):", len(keys_fallback), "->", keys_fallback[:5], "..." if len(keys_fallback) > 5 else "")
    print("So get_sprint_story_keys returns:", len(keys_sprint) if keys_sprint else len(keys_fallback), "keys (sprint branch used)" if keys_sprint else "(fallback used)")


def debug_statuses(project: str) -> None:
    """Print key -> status name for recent issues (to see exact Jira status names)."""
    import requests
    base = _get_base_url()
    email, token = _get_auth()
    if not base or not email or not token:
        print("Missing Jira env"); return
    jql = f'project = "{project}" ORDER BY updated DESC'
    url = f"{base}/rest/api/3/search/jql"
    r = requests.get(url, params={"jql": jql, "maxResults": 20, "fields": "key,status"}, auth=(email, token), headers={"Accept": "application/json"}, timeout=30)
    if r.status_code != 200:
        print("API error:", r.status_code, r.text[:300]); return
    for issue in (r.json().get("issues") or []):
        key = issue.get("key", "")
        status = (issue.get("fields") or {}).get("status") or {}
        name = status.get("name", "?")
        print(f"  {key} -> {name}")
    print("(Use status = \"<name>\" in JQL to filter to one column.)")


def main():
    args = [a for a in sys.argv[1:] if a != "--debug"]
    debug = "--debug" in sys.argv
    project = args[0] if args else "EC"
    print("JIRA_BASE_URL:", _get_base_url() or "(not set)")
    print("JIRA_USERNAME:", _get_auth()[0] or "(not set)")
    print("JIRA_API_TOKEN:", "set" if _get_auth()[1] else "(not set)")
    if debug:
        if "--status" in sys.argv:
            print("Debug: key -> status for project", project)
            debug_statuses(project)
        else:
            print("Debug: which JQL branch and counts for project", project)
            debug_jql(project)
        return
    print("Fetching issues for project:", project)
    keys, err = get_sprint_story_keys(project)
    if err:
        print("ERROR:", err)
        sys.exit(1)
    print("Keys:", keys if keys else "(none)")
    sys.exit(0 if keys else 1)


if __name__ == "__main__":
    main()

"""Unit tests for jira_fetch (mocked HTTP)."""
import os
import unittest
from unittest.mock import patch, MagicMock

# Ensure project root is on path
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from jira_fetch import get_sprint_story_keys, _get_base_url, _get_auth, _fetch_stories


class TestJiraFetch(unittest.TestCase):
    def setUp(self):
        self.env_patcher = patch.dict(
            os.environ,
            {
                "JIRA_BASE_URL": "https://test.atlassian.net",
                "JIRA_USERNAME": "user@test.com",
                "JIRA_API_TOKEN": "secret",
            },
            clear=False,
        )
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()

    def test_get_base_url_from_env(self):
        self.assertEqual(_get_base_url(), "https://test.atlassian.net")

    def test_get_base_url_strips_trailing_slash(self):
        with patch.dict(os.environ, {"JIRA_BASE_URL": "https://x.atlassian.net/"}, clear=False):
            self.assertEqual(_get_base_url(), "https://x.atlassian.net")

    def test_get_auth(self):
        self.assertEqual(_get_auth(), ("user@test.com", "secret"))

    def test_get_sprint_story_keys_missing_base_url(self):
        with patch.dict(os.environ, {"JIRA_BASE_URL": ""}, clear=False):
            keys, err = get_sprint_story_keys("EC")
        self.assertEqual(keys, [])
        self.assertIn("JIRA_BASE_URL", err)

    def test_get_sprint_story_keys_missing_credentials(self):
        with patch.dict(os.environ, {"JIRA_USERNAME": "", "JIRA_API_TOKEN": ""}, clear=False):
            keys, err = get_sprint_story_keys("EC")
        self.assertEqual(keys, [])
        self.assertIn("JIRA_USERNAME", err)

    @patch("jira_fetch.requests.get")
    def test_get_sprint_story_keys_success_sprint(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"issues": [{"key": "EC-298"}, {"key": "EC-318"}]},
        )
        keys, err = get_sprint_story_keys("EC")
        self.assertEqual(err, "")
        self.assertEqual(keys, ["EC-298", "EC-318"])
        self.assertEqual(mock_get.call_count, 1)
        call_url = mock_get.call_args[0][0]
        self.assertIn("/rest/api/3/search/jql", call_url, "Must use new /search/jql endpoint (old /search returns 410)")
        call_jql = mock_get.call_args[1]["params"]["jql"]
        self.assertIn("openSprints", call_jql)

    @patch("jira_fetch.requests.get")
    def test_get_sprint_story_keys_fallback_when_sprint_empty(self, mock_get):
        def side_effect(*args, **kwargs):
            jql = kwargs.get("params", {}).get("jql", "")
            if "openSprints" in jql:
                return MagicMock(status_code=200, json=lambda: {"issues": []})
            return MagicMock(status_code=200, json=lambda: {"issues": [{"key": "EC-298"}]})

        mock_get.side_effect = side_effect
        keys, err = get_sprint_story_keys("EC")
        self.assertEqual(err, "")
        self.assertEqual(keys, ["EC-298"])
        self.assertEqual(mock_get.call_count, 2)

    @patch("jira_fetch.requests.get")
    def test_get_sprint_story_keys_api_error(self, mock_get):
        mock_get.return_value = MagicMock(status_code=401, text="Unauthorized")
        keys, err = get_sprint_story_keys("EC")
        self.assertEqual(keys, [])
        self.assertIn("401", err)

    @patch("jira_fetch.requests.get")
    def test_get_sprint_story_keys_connection_error(self, mock_get):
        import requests
        mock_get.side_effect = requests.exceptions.ConnectionError("Failed to resolve")
        keys, err = get_sprint_story_keys("EC")
        self.assertEqual(keys, [])
        self.assertIn("Jira request failed", err)


if __name__ == "__main__":
    unittest.main()

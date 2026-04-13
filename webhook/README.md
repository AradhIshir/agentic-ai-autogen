# ISHIR Agentic AI QA Workflow – Webhook Server Setup Guide

## What it does
- Listens for Jira webhooks on `POST /jira-webhook`
- Fires when a User Story moves to **"In Progress"** → TestDesigner + AutomationAgent run → Email 1 sent
- Fires when a User Story moves to **"QA"** → ExecutionAgent + BugCreator run → Email 2 sent

---

## Step 1 – Confirm your `.env` has these values

```
# Who the notification email is sent TO (comma-separated if multiple)
WEBHOOK_NOTIFY_TO=agoyal@ishir.com

# Status names that trigger the pipeline (must match Jira exactly)
WEBHOOK_TRIGGER_STATUS=In Progress
WEBHOOK_TRIGGER_STATUS_QA=QA

# Optional: absolute path to the automation repo root (TestCases, ResultReport, generated_testscript).
# If unset, defaults to the parent folder of backend/UStoAutomationBug.py.
# AUTOMATION_PROJECT_ROOT=/path/to/AgenticAIAutogen

# Playwright MCP (ExecutionAgent): headless avoids "Failed to launch browser process" when the
# webhook subprocess has no GUI / DISPLAY (default is headless on).
PLAYWRIGHT_MCP_HEADLESS=true
# Isolated profile avoids "Browser is already in use for .../mcp-chrome" when Cursor or another MCP uses the same default profile.
PLAYWRIGHT_MCP_ISOLATED=true
# Optional explicit engine: chrome, firefox, webkit, msedge (only if needed)
# PLAYWRIGHT_MCP_BROWSER=chrome
# Linux/Docker only if Chromium still fails to start:
# PLAYWRIGHT_MCP_NO_SANDBOX=true
# To watch the browser locally while debugging:
# PLAYWRIGHT_MCP_HEADLESS=false
```

---

## Step 2 – Install dependencies

```bash
pip install -r webhook/requirements.txt
```

---

## Step 3 – Start the webhook server

```bash
bash webhook/start.sh
# or manually:
uvicorn webhook.server:app --host 0.0.0.0 --port 8000
```

Server starts at: `http://localhost:8000`

Health check: `http://localhost:8000/health`

---

## Step 4 – Start the ngrok tunnel

The agents save files to your local machine, so the webhook server must run
on the **same machine**. ngrok exposes it to the internet via a fixed public URL.

### 4a – Start the tunnel (run in a separate terminal)

```bash
ngrok http --domain=troublingly-cliquey-gabriel.ngrok-free.dev 8000
```

The tunnel stays active as long as this terminal is open.

### 4b – Your public webhook URL (fixed, never changes)

```
https://troublingly-cliquey-gabriel.ngrok-free.dev/jira-webhook
```

> **Note:** Because you have a static ngrok domain, this URL is permanent —
> no need to update the Jira webhook setting after restarts.

---

## Step 5 – Create the Jira webhook (one-time)

1. Go to **Jira Settings → System → WebHooks** (admin required)
2. Click **Create a WebHook**
3. Fill in:

   | Field | Value |
   |---|---|
   | Name | ISHIR Agentic AI QA Workflow |
   | URL | `https://troublingly-cliquey-gabriel.ngrok-free.dev/jira-webhook` |
   | Events | ☑ Issue → updated |
   | JQL Filter | `project = EC AND issuetype = Story` |

4. Click **Create**

Jira will POST to your URL every time a Story in the EC project is updated.
The server only acts when the status changes to **"In Progress"** or **"QA"**.

---

## Step 6 – Azure AD app permission for email (one-time)

Your Outlook credentials use **client credentials flow** (app-only, no user login).
The Azure app (`OUTLOOK_CLIENT_ID`) must have:

- **API permission**: `Microsoft Graph → Application permissions → Mail.Send`
- **Admin consent** granted

To check/add:
1. Azure Portal → **Entra ID → App registrations** → find your app (by CLIENT_ID)
2. **API permissions → Add a permission → Microsoft Graph → Application permissions → Mail.Send**
3. Click **Grant admin consent**

---

## Running all services together

Open three terminals:

```bash
# Terminal 1 – webhook server
bash webhook/start.sh

# Terminal 2 – ngrok tunnel (keep open)
ngrok http --domain=troublingly-cliquey-gabriel.ngrok-free.dev 8000

# Terminal 3 – Streamlit UI (unchanged)
streamlit run ui/app.py
```

---

## Pipeline flow summary

```
US → In Progress
  └─ testcases step  (QALead + TestDesigner)  → DB sync
  └─ automation step (QALead + AutomationAgent) → DB sync
  └─ Email 1 sent  ✅ Testcase & script Complete

US → QA
  └─ execute step   (QALead + ExecutionAgent) → DB sync
  └─ bugs step      (QALead + BugCreator)     → DB sync
  └─ Email 2 sent  ✅ QA Execution Complete
```

---

## Payload the webhook server handles

```json
{
  "webhookEvent": "jira:issue_updated",
  "issue": { "key": "EC-299", "fields": { "..." : "..." } },
  "changelog": {
    "items": [
      { "field": "status", "fromString": "To Do", "toString": "In Progress" }
    ]
  }
}
```

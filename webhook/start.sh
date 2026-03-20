#!/usr/bin/env bash
# Start the ISHIR QA Webhook Listener
# Usage: bash webhook/start.sh [PORT]
set -e

PORT="${1:-8000}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Activate .venv if present
if [ -f "$ROOT/.venv/bin/activate" ]; then
  source "$ROOT/.venv/bin/activate"
fi

# Install webhook deps if not already installed
pip install -q -r "$ROOT/webhook/requirements.txt"

echo "============================================="
echo " ISHIR QA Webhook Server"
echo " Listening on port $PORT"
echo "============================================="
echo " Public URL (ngrok – run in another terminal):"
echo "   ngrok http --domain=troublingly-cliquey-gabriel.ngrok-free.dev $PORT"
echo ""
echo " Jira webhook endpoint:"
echo "   https://troublingly-cliquey-gabriel.ngrok-free.dev/jira-webhook"
echo "============================================="

cd "$ROOT"
uvicorn webhook.server:app --host 0.0.0.0 --port "$PORT" --reload

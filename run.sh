#!/bin/sh
# Start the NavEasy API (port 8000) and web app (port 5173). Ctrl-C stops both.
cd "$(dirname "$0")" || exit 1
[ -d .venv ] || { echo "Create the environment first: python3 -m venv .venv && . .venv/bin/activate && pip install -r backend/requirements.txt"; exit 1; }
[ -d frontend/node_modules ] || (cd frontend && npm install)
.venv/bin/uvicorn backend.app.main:app --port 8000 &
API=$!
trap 'kill $API 2>/dev/null' EXIT INT TERM
(cd frontend && npm run dev -- --port 5173 --strictPort)

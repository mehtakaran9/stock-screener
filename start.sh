#!/bin/bash

# start.sh - Stock Screener Project
# Runs both backend and frontend concurrently.

# Function to kill background processes on exit
cleanup() {
    echo "Stopping servers..."
    kill $BACKEND_PID
    kill $FRONTEND_PID
    exit
}

trap cleanup SIGINT SIGTERM

echo "Ensuring port 8000 is clear..."
lsof -t -i:8000 | xargs kill -9 2>/dev/null

echo "Starting Stock Screener Backend (FastAPI)..."
source venv/bin/activate
export PYTHONPATH=$PYTHONPATH:.
uvicorn backend.main:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

echo "Starting Stock Screener Frontend (Vite)..."
cd frontend
npm run dev &
FRONTEND_PID=$!

echo "------------------------------------------------"
echo "Backend: http://localhost:8000"
echo "Frontend: http://localhost:5173"
echo "Press Ctrl+C to stop both servers."
echo "------------------------------------------------"

# Wait for background processes
wait

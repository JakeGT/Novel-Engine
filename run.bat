@echo off
echo Starting Novel Engine API...
uvicorn backend.main:app --reload
pause

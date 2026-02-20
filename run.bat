@echo off
echo Starting Novel Engine API...
uvicorn app.main:app --reload
pause

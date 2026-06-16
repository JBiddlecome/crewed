@echo off
rem Crewed — local dev server. Uses the Python where dependencies are installed.
cd /d "%~dp0"
"%~dp0venv\Scripts\python.exe" -m uvicorn main:app --reload
pause

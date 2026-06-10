@echo off
rem Crewed — local dev server. Uses the Python where dependencies are installed.
cd /d "%~dp0"
"C:\Users\jakeb\AppData\Local\Programs\Python\Python312\python.exe" -m uvicorn main:app --reload

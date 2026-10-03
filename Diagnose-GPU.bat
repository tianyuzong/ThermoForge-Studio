@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m strength.cuda_runtime
pause

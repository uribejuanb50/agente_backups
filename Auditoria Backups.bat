@echo off
rem Abre la auditoria sin ventana de consola
cd /d "%~dp0"
if exist "venv\Scripts\pythonw.exe" (
    start "" "venv\Scripts\pythonw.exe" "main.py"
) else (
    start "" pythonw "main.py"
)

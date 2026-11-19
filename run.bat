@echo off
chcp 65001 >nul
rem Запуск из исходников без сборки
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv
  call .venv\Scripts\activate.bat
  pip install -r requirements.txt
) else (
  call .venv\Scripts\activate.bat
)
start "" pythonw main.py

@echo off
chcp 65001 >nul
rem Сборка SysPulse.exe (нужен Python 3.10+ с галочкой "Add to PATH")
cd /d "%~dp0"
python -m venv .venv || goto :error
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt || goto :error
python -m pytest -q || echo Внимание: часть тестов не прошла
pyinstaller --noconfirm --clean SysPulse.spec || goto :error
echo.
echo Готово: dist\SysPulse.exe
pause
exit /b 0
:error
echo Ошибка сборки.
pause
exit /b 1

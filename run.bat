@echo off
echo ============================================
echo  Binance Perp Scanner - Starting...
echo ============================================
echo.
echo  Opening in your browser at http://localhost:8501
echo  Press Ctrl+C in this window to stop.
echo.
where python >nul 2>nul
if %errorlevel%==0 (
    set PY_CMD=python
) else (
    where py >nul 2>nul
    if %errorlevel%==0 (
        set PY_CMD=py
    ) else (
        echo Python was not found. Install Python 3.10+ from https://www.python.org/downloads/
        pause
        exit /b 1
    )
)
%PY_CMD% -m streamlit run scanner.py --server.headless false
pause

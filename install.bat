@echo off
echo ============================================
echo  Binance Perp Scanner - Installing...
echo ============================================
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
%PY_CMD% -m pip install --upgrade pip
%PY_CMD% -m pip install -r requirements.txt
echo.
echo ============================================
echo  Done! Double-click run.bat to start.
echo ============================================
pause

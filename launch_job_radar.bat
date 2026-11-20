@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "REPO_DIR=%SCRIPT_DIR%"
if "%REPO_DIR:~-1%"=="\" set "REPO_DIR=%REPO_DIR:~0,-1%"

cd /d "%REPO_DIR%"

set "PDEF_VENV_PYTHON=%REPO_DIR%\.venv\Scripts\python.exe"

if exist "%PDEF_VENV_PYTHON%" (
    if "%PDEF_DRY_RUN%"=="1" (
        echo "%PDEF_VENV_PYTHON%" -m streamlit run docs/job/job_radar_app.py --theme.primaryColor "#0f5f74"
        exit /b 0
    )
    "%PDEF_VENV_PYTHON%" -m streamlit run docs/job/job_radar_app.py --theme.primaryColor "#0f5f74"
    goto :after_run
)

echo [ERROR] No local .venv Python was found at:
echo         "%PDEF_VENV_PYTHON%"
echo Rebuild the local environment first, then relaunch Job Radar.
if not "%PDEF_DRY_RUN%"=="1" pause
exit /b 1

:after_run

if errorlevel 1 (
    echo.
    echo [ERROR] Job Radar did not start correctly.
    if not "%PDEF_DRY_RUN%"=="1" pause
    exit /b 1
)

endlocal
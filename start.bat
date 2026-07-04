@echo off
echo AutoDJ x Mixxx (patched fork)
echo.

if not exist "C:\dev\mixxx\build\mixxx.exe" (
    echo Patched Mixxx not found at C:\dev\mixxx\build\mixxx.exe
    echo Run build_mixxx.bat first ^(see BUILDING_MIXXX.md^).
    pause
    exit /b 1
)

tasklist /FI "IMAGENAME eq mixxx.exe" 2>NUL | find /I "mixxx.exe" >NUL
if errorlevel 1 (
    echo Starting patched Mixxx...
    start "" "C:\dev\mixxx\build\mixxx.exe"
    timeout /t 15 >nul
) else (
    echo Mixxx already running.
)

REM Prefer the standalone exe (build_exe.bat); fall back to Python source.
REM Run from backend\ so .env, music\, and autodj.db resolve the same either way.
cd /d "%~dp0backend"
if exist "%~dp0dist\autodj\autodj.exe" (
    "%~dp0dist\autodj\autodj.exe"
) else (
    python autodj.py
)

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

cd /d "%~dp0backend"
python autodj.py

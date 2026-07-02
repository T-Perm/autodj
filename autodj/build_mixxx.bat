@echo off
REM Rebuild the patched Mixxx fork after source changes or an upstream sync.
REM First-time setup is documented in BUILDING_MIXXX.md.

set MIXXX_SRC=C:\dev\mixxx
set VS_BT=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools
set CMAKE=%VS_BT%\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe

if not exist "%MIXXX_SRC%\build\CMakeCache.txt" (
    echo Build directory not configured. See BUILDING_MIXXX.md for first-time setup.
    pause
    exit /b 1
)

call "%VS_BT%\VC\Auxiliary\Build\vcvars64.bat"
"%CMAKE%" --build "%MIXXX_SRC%\build" --target mixxx
if errorlevel 1 (
    echo BUILD FAILED
    pause
    exit /b 1
)
echo.
echo Build OK: %MIXXX_SRC%\build\mixxx.exe

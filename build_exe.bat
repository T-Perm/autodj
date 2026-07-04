@echo off
REM Builds dist\autodj\autodj.exe (standalone - no Python needed on the target machine).
cd /d "%~dp0"

python -c "import PyInstaller" 2>NUL || (
    echo Installing PyInstaller...
    pip install pyinstaller
)

echo Building AutoDJ executable...
pyinstaller --noconfirm autodj.spec || (
    echo Build failed. See output above.
    pause
    exit /b 1
)

REM .env holds the API key and stays OUT of the bundle - copy the template next to the exe.
if exist ".env" copy /y ".env" "dist\autodj\.env" >NUL

echo.
echo Done. Run:  dist\autodj\autodj.exe
pause

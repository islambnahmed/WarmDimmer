@echo off
rem Builds a single-file WarmDimmer.exe into .\dist (needs: pip install pyinstaller)
cd /d "%~dp0"
pip install pyinstaller >nul
pyinstaller --noconsole --onefile --name WarmDimmer --icon warmdimmer\assets\icon.ico --clean WarmDimmer.pyw
echo.
echo Done: dist\WarmDimmer.exe
pause

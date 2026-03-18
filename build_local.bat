@echo off
setlocal

echo =========================
echo Building iVCS Control Panel
echo =========================
echo.

if exist venv rmdir /s /q venv
py -m venv venv
call venv\Scripts\activate.bat

python -m pip install --upgrade pip
pip install -r requirements.txt

pyinstaller iVCS-iVSA_control_Panel.spec

echo.
echo Build complete.
echo Check the dist folder.
echo.

endlocal
pause
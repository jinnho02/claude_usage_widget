@echo off
setlocal
cd /d "%~dp0"
set "DEST=%LOCALAPPDATA%\Programs\ClaudeUsage"

if not exist "claude_usage_widget.pyw" (
  echo claude_usage_widget.pyw must be in the same folder as this file.
  goto :fail
)

echo [1/4] Installing packages...
py -m pip install --upgrade curl_cffi keyring || goto :fail

echo [2/4] Copying widget to %DEST% ...
taskkill /f /im ClaudeUsage.exe >nul 2>&1
set "PIDF=%APPDATA%\ClaudeUsageWidget\pid.txt"
if exist "%PIDF%" for /f "usebackq delims=" %%p in ("%PIDF%") do taskkill /f /fi "PID eq %%p" /fi "IMAGENAME eq pythonw.exe" >nul 2>&1
timeout /t 1 /nobreak >nul
if not exist "%DEST%" mkdir "%DEST%"
if exist "%DEST%\ClaudeUsage.exe" del /q "%DEST%\ClaudeUsage.exe"
copy /y "claude_usage_widget.pyw" "%DEST%\claude_usage_widget.pyw" >nul || goto :fail

echo [3/4] Finding pythonw.exe ...
set "PYW="
for /f "delims=" %%i in ('py -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"') do set "PYW=%%i"
if not defined PYW goto :fail
if not exist "%PYW%" (
  echo pythonw.exe not found: %PYW%
  goto :fail
)

echo [4/4] Adding Start menu shortcut...
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Programs')+'\Claude Usage.lnk'); $s.TargetPath='%PYW%'; $s.Arguments='\"%DEST%\claude_usage_widget.pyw\"'; $s.WorkingDirectory='%DEST%'; $s.Save()"

start "" "%PYW%" "%DEST%\claude_usage_widget.pyw"
echo.
echo Done. The widget is now on your desktop.
echo You can close this window.
pause
exit /b 0

:fail
echo.
echo Something went wrong. Please check the messages above.
pause
exit /b 1

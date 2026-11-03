@echo off
setlocal
cd /d "%~dp0"
set "DEST=%LOCALAPPDATA%\Programs\ClaudeUsage"
set "PIDF=%APPDATA%\ClaudeUsageWidget\pid.txt"

echo [1/4] Installing packages...
py -m pip install --upgrade -r requirements.txt || goto :fail

echo [2/4] Copying widget...
if exist "%PIDF%" for /f "usebackq delims=" %%p in ("%PIDF%") do taskkill /f /fi "PID eq %%p" /fi "IMAGENAME eq pythonw.exe" >nul 2>&1
timeout /t 1 /nobreak >nul
if not exist "%DEST%" mkdir "%DEST%"
copy /y "claude_usage_widget.pyw" "%DEST%\claude_usage_widget.pyw" >nul || goto :fail

echo [3/4] Finding pythonw.exe...
set "PYW="
for /f "delims=" %%i in ('py -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"') do set "PYW=%%i"
if not exist "%PYW%" goto :fail

echo [4/4] Adding Start menu shortcut...
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Programs')+'\Claude Usage.lnk'); $s.TargetPath='%PYW%'; $s.Arguments='\"%DEST%\claude_usage_widget.pyw\"'; $s.WorkingDirectory='%DEST%'; $s.Save()"

start "" "%PYW%" "%DEST%\claude_usage_widget.pyw"
echo Done.
pause
exit /b 0

:fail
echo Install failed.
pause
exit /b 1

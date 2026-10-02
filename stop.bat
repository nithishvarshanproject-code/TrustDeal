@echo off
rem TrustDeal: close the backend and frontend windows and free ports 8000 and 5173.
rem The Telegram bot runs inside the backend, so closing the backend also stops it.
setlocal
cd /d "%~dp0"

echo.
echo Stopping TrustDeal ^(backend with the Telegram bot, and frontend^)...

rem 1) Close the two windows started by start.bat, including every process inside them
rem    (uvicorn's auto-reloader and its worker, npm and the Vite node process).
rem    They are found by the command line start.bat gave them, not by window title:
rem    on Windows 11 the windows are Windows Terminal tabs, which have no title of their own.
call :close_window "TrustDeal - Backend" "*/k*backend.main*"
call :close_window "TrustDeal - Frontend" "*/k*npm run dev*"

rem 2) Free the ports, whatever is still listening on them.
call :free_port 8000
call :free_port 5173

rem 3) Report.
set "busy="
call :still_busy 8000 && set "busy=1"
call :still_busy 5173 && set "busy=1"
if defined busy (
    echo.
    echo WARNING: a port is still in use. Close the program using it, or run stop.bat again.
    exit /b 1
)
echo   Ports 8000 and 5173 are free.
echo Done.
exit /b 0

:close_window
rem %1 = window name (for messages), %2 = command-line pattern of its cmd.exe
rem (the helper cmd running this PowerShell query contains the pattern too, so skip it;
rem  no double quotes inside the query: for /f would split it at the '=' signs)
set "found="
for /f %%p in ('powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'cmd.exe' -and $_.CommandLine -like '%~2' -and $_.CommandLine -notlike '*powershell*' } | ForEach-Object { $_.ProcessId }"') do (
    taskkill /PID %%p /T /F >nul 2>&1
    set "found=1"
)
if defined found (echo   Closed window "%~1".) else (echo   No "%~1" window open.)
exit /b 0

:free_port
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R /C:":%1 .*LISTENING"') do (
    if not "%%p"=="0" (
        taskkill /PID %%p /T /F >nul 2>&1 && echo   Stopped process %%p on port %1.
    )
)
exit /b 0

:still_busy
rem errorlevel 0 if something is still LISTENING on port %1 (waits up to ~5 s for it to close)
set /a n=0
:still_busy_loop
netstat -ano | findstr /R /C:":%1 .*LISTENING" >nul || exit /b 1
set /a n+=1
if %n% geq 5 exit /b 0
ping -n 2 127.0.0.1 >nul
goto still_busy_loop

@echo off
rem TrustDeal launcher: backend (FastAPI + MeTTa) and frontend (Vite), each in its own window.
rem The Telegram channel runs inside the backend (long polling) when omega\omega.env has a
rem TELEGRAM_BOT_TOKEN; the backend reads the token itself, it is never put on a command line.
rem   start.bat          start with the existing data
rem   start.bat reset    reset the demo data first (python -m data.seed)
rem   start.bat omega    decisions run inside the Omega agent (start it first: omega\start-omega.bat)
rem   options can be combined, e.g. start.bat reset omega
setlocal
cd /d "%~dp0"

set "RESET="
set "OMEGA="
for %%a in (%*) do (
    if /i "%%a"=="reset" set "RESET=1"
    if /i "%%a"=="omega" set "OMEGA=1"
)

set "BACKEND_URL=http://127.0.0.1:8000/health"
set "FRONTEND_URL=http://localhost:5173"

echo.
echo ============================================
echo   TrustDeal - starting
echo ============================================
echo.

rem ---------- checks ----------
echo [1/6] Checking the environment...
if not exist ".venv\Scripts\python.exe" goto no_venv
where curl >nul 2>&1 || goto no_curl
where npm >nul 2>&1 || goto no_npm
call :port_in_use 8000 && goto port_busy_8000
call :port_in_use 5173 && goto port_busy_5173
echo       OK: .venv found, ports 8000 and 5173 are free.

rem ---------- optional data reset ----------
if defined RESET (
    echo [2/6] Resetting demo data...
    ".venv\Scripts\python.exe" -m data.seed || goto seed_failed
) else (
    echo [2/6] Keeping existing data. Use "start.bat reset" for fresh demo data.
)

rem ---------- frontend dependencies ----------
if exist "frontend\node_modules" (
    echo [3/6] Frontend dependencies already installed.
) else (
    echo [3/6] frontend\node_modules missing - running npm install...
    pushd frontend
    call npm install || goto npm_install_failed
    popd
)

rem ---------- backend ----------
rem The backend window inherits ENGINE_RUNNER (and OMEGA_TOKEN in omega mode).
if defined OMEGA (
    call :load_omega_token || goto no_omega_env
    set "ENGINE_RUNNER=omega"
) else (
    set "ENGINE_RUNNER=local"
)
echo [4/6] Starting backend in window "TrustDeal - Backend"...
start "TrustDeal - Backend" /D "%~dp0." cmd /k ".venv\Scripts\python.exe -m backend.main"
set /a tries=0
:wait_backend
curl -s -f "%BACKEND_URL%" >nul 2>&1 && goto backend_ok
set /a tries+=1
if %tries% geq 30 goto backend_timeout
ping -n 2 127.0.0.1 >nul
goto wait_backend
:backend_ok
echo       Backend is up: %BACKEND_URL%
call :telegram_status
if defined OMEGA call :wait_omega

rem ---------- frontend ----------
echo [5/6] Starting frontend in window "TrustDeal - Frontend"...
start "TrustDeal - Frontend" /D "%~dp0frontend" cmd /k "npm run dev"
set /a tries=0
:wait_frontend
curl -s -f "%FRONTEND_URL%" >nul 2>&1 && goto frontend_ok
set /a tries+=1
if %tries% geq 60 goto frontend_timeout
ping -n 2 127.0.0.1 >nul
goto wait_frontend
:frontend_ok
echo       Frontend is up: %FRONTEND_URL%

rem ---------- browser ----------
echo [6/6] Opening %FRONTEND_URL% in your browser...
start "" "%FRONTEND_URL%"
echo.
echo TrustDeal is running. Close it with stop.bat
echo.
exit /b 0

rem ---------- helpers ----------
:load_omega_token
rem Reads DEALDESK_TOKEN from omega\omega.env into OMEGA_TOKEN (never printed).
if not exist "omega\omega.env" exit /b 1
for /f "usebackq tokens=1,* delims==" %%a in ("omega\omega.env") do (
    if "%%a"=="DEALDESK_TOKEN" set "OMEGA_TOKEN=%%b"
)
if not defined OMEGA_TOKEN exit /b 1
exit /b 0

:telegram_status
rem The backend reports whether the Telegram channel is on (it never returns the token).
curl -s "http://127.0.0.1:8000/seller/agent/telegram" | findstr /R "enabled.:true" >nul && (
    echo       Telegram: on ^(long polling, no public URL^). Connect chats on the Customer page and in the Agent inbox.
) || (
    echo       Telegram: off ^(no TELEGRAM_BOT_TOKEN in omega\omega.env - see README, Telegram channel^).
)
exit /b 0

:wait_omega
echo       Engine: Omega agent. Waiting for the Deal Desk plugin to connect...
set /a otries=0
:wait_omega_loop
curl -s "http://127.0.0.1:8000/omega/status" | findstr /R "rules_match.:true" >nul && goto omega_ok
set /a otries+=1
if %otries% geq 30 goto omega_missing
ping -n 2 127.0.0.1 >nul
goto wait_omega_loop
:omega_ok
echo       Omega agent connected and running the same rule files.
exit /b 0
:omega_missing
echo.
echo WARNING: the Omega agent is not connected, or loaded different rule files.
echo          Decisions will return a clear "Omega unavailable" error - there is no local fallback.
echo          Start Omega with:  omega\start-omega.bat          (harness, no API key)
echo                        or:  omega\start-omega.bat agent    (full agent)
echo          Details: http://127.0.0.1:8000/omega/status
echo.
exit /b 0

:port_in_use
rem errorlevel 0 if something is LISTENING on port %1
netstat -ano | findstr /R /C:":%1 .*LISTENING" >nul
exit /b %errorlevel%

rem ---------- errors ----------
:no_omega_env
echo.
echo ERROR: omega\omega.env with a DEALDESK_TOKEN was not found.
echo        Run omega\start-omega.bat once first - it creates the file and the token.
exit /b 1

:no_venv
echo.
echo ERROR: .venv not found. Create it first, from this folder:
echo     python -m venv .venv
echo     .venv\Scripts\python -m pip install -r requirements.txt
exit /b 1

:no_curl
echo.
echo ERROR: curl.exe not found. It ships with Windows 10 1803 and later.
exit /b 1

:no_npm
echo.
echo ERROR: npm not found. Install Node.js from https://nodejs.org and try again.
exit /b 1

:port_busy_8000
echo.
echo ERROR: port 8000 is already in use - the backend may still be running.
echo        Run stop.bat first, then start.bat again.
exit /b 1

:port_busy_5173
echo.
echo ERROR: port 5173 is already in use - the frontend may still be running.
echo        Run stop.bat first, then start.bat again.
exit /b 1

:seed_failed
echo.
echo ERROR: resetting the demo data failed - see the messages above.
exit /b 1

:npm_install_failed
popd
echo.
echo ERROR: npm install failed - see the messages above.
exit /b 1

:backend_timeout
echo.
echo ERROR: the backend did not answer %BACKEND_URL% within 30 seconds.
echo        Check the "TrustDeal - Backend" window for errors, then run stop.bat.
exit /b 1

:frontend_timeout
echo.
echo ERROR: the frontend did not answer %FRONTEND_URL% within 60 seconds.
echo        Check the "TrustDeal - Frontend" window for errors, then run stop.bat.
exit /b 1

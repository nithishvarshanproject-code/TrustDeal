@echo off
rem Start the Omega side of Deal Desk.
rem   omega\start-omega.bat            harness mode: PeTTa + Deal Desk plugin, no LLM, no API key
rem   omega\start-omega.bat agent      full OmegaClaw agent (needs ASIONE_API_KEY in omega\omega.env)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-omega.ps1" %*
exit /b %errorlevel%

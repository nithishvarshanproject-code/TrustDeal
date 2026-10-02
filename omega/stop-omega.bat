@echo off
rem Stop and remove the Omega containers (harness and agent). The agent's memory volume
rem (omegaclaw-memory) is kept.
echo Stopping Omega containers...
set "removed="
for %%c in (omegaclaw-harness omegaclaw) do (
    docker inspect %%c >nul 2>&1 && (
        docker rm -f %%c >nul 2>&1
        echo   Removed %%c.
        set "removed=1"
    )
)
if not defined removed echo   No Omega container was running.
echo Done. The omegaclaw-memory volume is kept.
exit /b 0

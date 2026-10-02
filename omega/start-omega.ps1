# Start the Omega side of Deal Desk.
#   harness : PeTTa + the Deal Desk plugin only (no agent loop, no LLM, no API key needed)
#   agent   : the full OmegaClaw agent (IRC chat, ASI:One LLM) with the Deal Desk plugin
# Secrets live in omega\omega.env and are never printed.
param([ValidateSet("harness", "agent")][string]$Mode = "harness")

# Native commands report through $LASTEXITCODE; "Stop" would turn their stderr into exceptions.
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
$envFile = Join-Path $PSScriptRoot "omega.env"
$example = Join-Path $PSScriptRoot "omega.env.example"
$image = "singularitynet/omega:v0.1.19"
$wsUrl = "ws://host.docker.internal:8000/omega/engine"
$ircChannel = "##DealDeskNithish2026"
# ASI:One chat model for the agent's own LLM loop (the image defaults to asi1-ultra).
$asiModel = "asi1"

function Fail($message) { Write-Host ""; Write-Host "ERROR: $message" -ForegroundColor Red; exit 1 }

function New-Secret([int]$bytes) {
    $buffer = New-Object byte[] $bytes
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($buffer)
    return ($buffer | ForEach-Object { $_.ToString("x2") }) -join ""
}

function New-AuthCode {
    $buffer = New-Object byte[] 6
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($buffer)
    return ($buffer | ForEach-Object { [string]($_ % 10) }) -join ""
}

# ---- read / complete omega.env without printing any value ----
function Read-EnvFile {
    $values = @{}
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { $values[$Matches[1]] = $Matches[2].Trim() }
    }
    return $values
}

function Set-EnvValue($name, $value) {
    $lines = Get-Content $envFile
    $found = $false
    $lines = $lines | ForEach-Object {
        if ($_ -match "^\s*$name=") { $found = $true; "$name=$value" } else { $_ }
    }
    if (-not $found) { $lines += "$name=$value" }
    Set-Content -Path $envFile -Value $lines -Encoding ascii
}

Write-Host ""
Write-Host "TrustDeal - starting Omega ($Mode mode)"

docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail "Docker is not running. Start Docker Desktop and try again." }
docker image inspect $image *> $null
if ($LASTEXITCODE -ne 0) { Fail "Image $image not found. Run: docker pull $image" }

if (-not (Test-Path $envFile)) {
    Copy-Item $example $envFile
    Write-Host "  Created omega\omega.env from omega.env.example"
}
$envValues = Read-EnvFile
if (-not $envValues["DEALDESK_TOKEN"]) { Set-EnvValue "DEALDESK_TOKEN" (New-Secret 24); Write-Host "  Generated DEALDESK_TOKEN" }
if (-not $envValues["OMEGACLAW_AUTH_SECRET"]) { Set-EnvValue "OMEGACLAW_AUTH_SECRET" (New-AuthCode); Write-Host "  Generated OMEGACLAW_AUTH_SECRET" }
$envValues = Read-EnvFile
$env:DEALDESK_TOKEN = $envValues["DEALDESK_TOKEN"]   # passed by name (-e DEALDESK_TOKEN), never on a command line

# Mounted under /PeTTa: the agent's Landlock policy (profile/policy.yaml) only allows reads there.
$engineMount = "${root}\engine:/PeTTa/dealdesk/engine:ro"
$pluginMount = "${root}\omega\dealdesk_plugin:/PeTTa/dealdesk/plugin:ro"

# Only one Omega side may be connected at a time.
docker rm -f omegaclaw-harness omegaclaw *> $null

if ($Mode -eq "harness") {
    docker run -d --name omegaclaw-harness `
        --security-opt no-new-privileges:true --init `
        --add-host=host.docker.internal:host-gateway `
        -v $engineMount -v $pluginMount `
        -e DEALDESK_TOKEN -e "DEALDESK_WS_URL=$wsUrl" `
        --entrypoint bash $image -c "cd /PeTTa && sh run.sh /PeTTa/dealdesk/plugin/harness.metta silent" | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "docker run failed (harness)." }
    Write-Host "  Started container omegaclaw-harness (PeTTa + TrustDeal plugin, no LLM)."
}
else {
    if (-not $envValues["ASIONE_API_KEY"]) {
        Fail "ASIONE_API_KEY is empty. Open omega\omega.env in an editor, type your ASI:One key after ASIONE_API_KEY= and save. Then run: omega\start-omega.bat agent"
    }
    # The agent's entrypoint keeps only allowlisted env vars, so the plugin reads its token from a
    # file holding only the token, in a writable git-ignored folder mounted under /tmp (which the
    # agent's Landlock policy allows). The plugin deletes the file right after reading it, before
    # the agent loop starts, so the agent's own tools cannot read it. omega.env is never mounted.
    $secretDir = Join-Path $PSScriptRoot "runtime_secret"
    New-Item -ItemType Directory -Force $secretDir | Out-Null
    Remove-Item (Join-Path $PSScriptRoot "dealdesk_token") -ErrorAction SilentlyContinue   # old location
    Set-Content -Path (Join-Path $secretDir "token") -Value $envValues["DEALDESK_TOKEN"] -NoNewline -Encoding ascii
    # Only what the container's gateway needs, passed by name (never on a command line).
    # DEALDESK_TOKEN is deliberately NOT in the agent container's environment.
    $env:ASIONE_API_KEY = $envValues["ASIONE_API_KEY"]
    $env:OMEGACLAW_AUTH_SECRET = $envValues["OMEGACLAW_AUTH_SECRET"]
    # Same flags as the official OmegaClaw installer (scripts/omegaclaw start), plus the Deal Desk mounts.
    docker run -d -t --name omegaclaw `
        --security-opt no-new-privileges:true --init `
        --add-host=host.docker.internal:host-gateway `
        --tmpfs /tmp:size=64m,mode=1777 --tmpfs /var/tmp:size=64m,mode=1777 --tmpfs /run:size=16m,mode=755 `
        --volume omegaclaw-memory:/PeTTa/repos/OmegaClaw-Core/memory `
        -v $engineMount -v $pluginMount -v "${secretDir}:/tmp/dealdesk-secret" `
        -v "${root}\omega\plugins.yaml:/PeTTa/repos/OmegaClaw-Core/config/plugins.yaml:ro" `
        -e ASIONE_API_KEY -e OMEGACLAW_AUTH_SECRET `
        -e IMPORT_KB_ON_START=0 -e "DEALDESK_WS_URL=$wsUrl" `
        $image `
        "commchannel=irc" "provider=ASIOne" "model=$asiModel" "embeddingprovider=Local" `
        "securityPolicyPath=/PeTTa/repos/OmegaClaw-Core/profile/policy.yaml" `
        "IRC_channel=$ircChannel" | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "docker run failed (agent)." }
    Write-Host "  Started container omegaclaw (OmegaClaw agent: IRC $ircChannel, ASI:One model $asiModel) with the TrustDeal plugin."
    Write-Host "  Chat: join $ircChannel on QuakeNet (https://webchat.quakenet.org/) and send once:"
    Write-Host "        auth <the OMEGACLAW_AUTH_SECRET value in omega\omega.env>"
}

Write-Host ""
Write-Host "  The plugin connects to the backend at $wsUrl (it retries until the backend is up)."
Write-Host "  Start the app with:  start.bat omega      Check:  http://localhost:8000/omega/status"
Write-Host "  Logs:  docker logs -f $(if ($Mode -eq 'harness') { 'omegaclaw-harness' } else { 'omegaclaw' })"
exit 0

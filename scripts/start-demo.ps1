param([int]$TimeoutSeconds = 45)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$apiRoot = Join-Path $projectRoot 'backend'
$uiRoot = Join-Path $projectRoot 'frontend'
$pythonExe = Join-Path $apiRoot 'venv/Scripts/python.exe'
$viteEntry = Join-Path $uiRoot 'node_modules/vite/bin/vite.js'
if (!(Test-Path -LiteralPath $pythonExe) -or !(Test-Path -LiteralPath $viteEntry)) {
    throw 'Install backend requirements and run npm ci in frontend first; see docs/RUNNING.md.'
}
$nodeExe = (Get-Command node -ErrorAction Stop).Source
$logRoot = Join-Path $apiRoot 'var'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
function Test-Endpoint([string]$Url, [string]$Expected) {
    try { $response = Invoke-WebRequest $Url -TimeoutSec 2 -UseBasicParsing; return $response.StatusCode -eq 200 -and $response.Content.Contains($Expected) }
    catch { return $false }
}
function Wait-Endpoint([string]$Url, [string]$Expected) {
    $until = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $until) {
        if (Test-Endpoint $Url $Expected) { return }
        Start-Sleep -Milliseconds 500
    }
    throw "Service did not become ready: $Url. See backend/var/local-*.err.log."
}
$launched = @()
if (!(Test-Endpoint 'http://127.0.0.1:8000/health' 'healthy')) {
    $apiProcess = Start-Process -FilePath $pythonExe -ArgumentList '-X','utf8','-m','uvicorn','app.api.main:app','--host','127.0.0.1','--port','8000' -WorkingDirectory $apiRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logRoot 'local-api.log') -RedirectStandardError (Join-Path $logRoot 'local-api.err.log')
    $launched += @{ service = 'api'; pid = $apiProcess.Id }
}
Wait-Endpoint 'http://127.0.0.1:8000/ready' 'ready'
if (!(Test-Endpoint 'http://127.0.0.1:5173/' 'id="app"')) {
    & $nodeExe (Join-Path $uiRoot 'scripts/component-types.mjs')
    if ($LASTEXITCODE -ne 0) { throw 'Frontend component type preparation failed.' }
    # Quote the executable argument explicitly for paths containing spaces.
    $uiProcess = Start-Process -FilePath $nodeExe -ArgumentList "`"$viteEntry`" --host 127.0.0.1 --port 5173 --strictPort" -WorkingDirectory $uiRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logRoot 'local-ui.log') -RedirectStandardError (Join-Path $logRoot 'local-ui.err.log')
    $launched += @{ service = 'ui'; pid = $uiProcess.Id }
}
Wait-Endpoint 'http://127.0.0.1:5173/' 'id="app"'
& (Join-Path $PSScriptRoot 'check-demo.ps1')
if ($launched.Count) { $launched | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logRoot 'demo-processes.json') -Encoding utf8 }
Write-Output 'Ready: http://127.0.0.1:5173/ (no generation was started).'

param(
    [string]$FrontendUrl = 'http://127.0.0.1:5173',
    [string]$BackendUrl = 'http://127.0.0.1:8000'
)

$ErrorActionPreference = 'Stop'
# Read-only local checks: no model, map queries, task creation or trip writes.
$health = Invoke-RestMethod "$($BackendUrl.TrimEnd('/'))/health" -TimeoutSec 10
if ($health.status -ne 'healthy') { throw 'Backend health check failed' }
$ready = Invoke-RestMethod "$($BackendUrl.TrimEnd('/'))/ready" -TimeoutSec 10
if ($ready.status -ne 'ready') { throw 'Local storage/executor is not ready' }
$page = Invoke-WebRequest "$($FrontendUrl.TrimEnd('/'))/" -TimeoutSec 10 -UseBasicParsing
if ($page.StatusCode -ne 200 -or $page.Content -notmatch 'id="app"') {
    throw 'Frontend entry is unavailable'
}
Write-Output 'PASS: frontend entry, backend health, local storage/executor readiness.'
Write-Output 'This does not verify browser rendering or model/map provider availability.'

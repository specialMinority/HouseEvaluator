# Keep the legacy parameter so the existing scheduled task remains compatible.
param([string]$Python = 'C:\Python314\python.exe')
$ErrorActionPreference = 'Stop'
$taskRepo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$taskCompose = Join-Path $taskRepo 'compose.worker.yaml'
$taskToken = Join-Path $taskRepo '.runtime/worker-token.txt'
if (-not (Test-Path -LiteralPath $taskToken -PathType Leaf)) { throw 'Private worker-token.txt is required.' }
# A completed legacy scheduled task can leave its child Python process alive.
# Refuse mixed execution; never kill a process based on an old PID file.
$taskLegacyWorkers = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" | Where-Object {
    $_.CommandLine -match '(?:^|\s)-m\s+backend\.v2\.remote_worker(?:\s|$)'
})
if ($taskLegacyWorkers.Count -gt 0) { throw 'A legacy host Python worker is still running. Verify its identity and stop it before starting isolated execution.' }
$taskDocker = (Get-Command docker -ErrorAction Stop).Source
# Docker Desktop may need time after logon. Never fall back to host Python.
$taskReady = $false
for ($taskAttempt = 0; $taskAttempt -lt 12; $taskAttempt++) {
    & $taskDocker info --format '{{.ServerVersion}}' 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $taskReady = $true; break }
    Start-Sleep -Seconds 10
}
if (-not $taskReady) { throw 'Docker Desktop is unavailable. The query worker remains stopped.' }
& $taskDocker compose -f $taskCompose up -d --no-build --pull never
if ($LASTEXITCODE -ne 0) { throw 'Isolated worker did not start. No host process was launched.' }
Write-Output 'Isolated query worker started. Stop it with scripts/stop_personal_worker.ps1.'

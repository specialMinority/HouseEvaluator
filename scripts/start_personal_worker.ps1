param(
    [string]$Python = 'C:\Python314\python.exe'
)
$ErrorActionPreference = 'Stop'
$taskRepo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$taskRuntime = Join-Path $taskRepo '.runtime'
$taskToken = Join-Path $taskRuntime 'worker-token.txt'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Python executable is unavailable.' }
if (-not (Test-Path -LiteralPath $taskToken -PathType Leaf)) { throw 'Private worker-token.txt is required.' }
$taskMutex = [System.Threading.Mutex]::new($false, 'Local\HouseEvaluatorPersonalWorker')
$taskOwned = $false
try {
    try { $taskOwned = $taskMutex.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] { $taskOwned = $true }
    if (-not $taskOwned) { Write-Output 'Worker launcher is already running.'; exit 0 }
    $env:HOUSE_EVALUATOR_WORKER_ORIGIN = 'https://houseevaluator-personal.onrender.com'
    $env:HOUSE_EVALUATOR_WORKER_TOKEN_FILE = $taskToken
    $env:HOUSE_EVALUATOR_SEARCH_SOURCE = 'suumo'
    $env:HOUSE_EVALUATOR_IMPORT_SOURCES = 'chintai,yahoo_realestate'
    $taskWorker = Start-Process -FilePath $Python -ArgumentList @('-m', 'backend.v2.remote_worker') `
        -WorkingDirectory $taskRepo -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $taskRuntime 'worker.stdout.log') `
        -RedirectStandardError (Join-Path $taskRuntime 'worker.stderr.log')
    Set-Content -LiteralPath (Join-Path $taskRuntime 'worker.pid') -Value $taskWorker.Id -Encoding ascii
    Set-Content -LiteralPath (Join-Path $taskRuntime 'worker-launcher.pid') -Value $PID -Encoding ascii
    $taskWorker.WaitForExit()
    exit $taskWorker.ExitCode
}
finally {
    if ($taskOwned) { $taskMutex.ReleaseMutex() }
    $taskMutex.Dispose()
}

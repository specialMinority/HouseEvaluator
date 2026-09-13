$ErrorActionPreference = 'Stop'
$taskCompose = Join-Path (Split-Path $PSScriptRoot -Parent) 'compose.worker.yaml'
& docker compose -f $taskCompose stop
if ($LASTEXITCODE -ne 0) { throw 'Could not confirm the isolated worker stopped.' }
Write-Output 'Query worker and egress gateway stopped; usage budgets were preserved.'

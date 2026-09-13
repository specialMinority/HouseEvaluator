param([string]$Python = 'C:\Python314\python.exe')
$ErrorActionPreference = 'Stop'
$taskName = 'HouseEvaluator Public Query Worker'
$taskScript = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'start_personal_worker.ps1')).Path
if (-not (Test-Path -LiteralPath $Python -PathType Leaf) -or $Python.Contains('"')) { throw 'Invalid Python path.' }
$taskShell = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
if (-not $taskShell) { $taskShell = Join-Path $PSHOME 'powershell.exe' }
$taskArgs = '-NoProfile -NonInteractive -WindowStyle Hidden -File "' + $taskScript + '" -Python "' + $Python + '"'
$taskExisting = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($taskExisting -and ($taskExisting.Actions.Count -ne 1 -or $taskExisting.Actions[0].Execute -ne $taskShell -or $taskExisting.Actions[0].Arguments -ne $taskArgs)) {
    throw 'A different task already uses this name. It was not modified.'
}
$taskUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$taskAction = New-ScheduledTaskAction -Execute $taskShell -Argument $taskArgs -WorkingDirectory (Split-Path $PSScriptRoot -Parent)
$taskTrigger = New-ScheduledTaskTrigger -AtLogOn -User $taskUser
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType Interactive -RunLevel Limited
$taskSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -Hidden
$taskDefinition = New-ScheduledTask -Action $taskAction -Trigger $taskTrigger -Principal $taskPrincipal -Settings $taskSettings `
    -Description 'HouseEvaluator fixed query worker. Requires this signed-in PC and private token file.'
Register-ScheduledTask -TaskName $taskName -InputObject $taskDefinition -Force | Out-Null
Write-Output 'Registered HouseEvaluator worker for the current user logon. No password was stored.'

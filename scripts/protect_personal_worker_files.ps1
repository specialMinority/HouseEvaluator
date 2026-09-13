$ErrorActionPreference = 'Stop'
$taskRepo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$taskRuntime = Join-Path $taskRepo '.runtime'
$taskIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
if ($taskIdentity.Name -match 'CodexSandbox') { throw 'Run as the signed-in operator account.' }
$taskBackupPath = Join-Path $taskRuntime 'private-files-acl-backup.json'
$taskBackups = @()
$taskTargets = @('worker-token.txt', 'render-access-code.txt', 'render-access-code.next.txt')
foreach ($taskName in $taskTargets) {
    $taskPath = Join-Path $taskRuntime $taskName
    if (-not (Test-Path -LiteralPath $taskPath -PathType Leaf)) { continue }
    $taskPart = Get-Item -LiteralPath $taskPath -Force
    while ($null -ne $taskPart) {
        if ($taskPart.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse paths are not supported.' }
        $taskPart = if ($taskPart -is [IO.FileInfo]) { $taskPart.Directory } else { $taskPart.Parent }
    }
    $taskBackups += @{name=$taskName; sddl=(Get-Acl -LiteralPath $taskPath).Sddl}
}
if (-not (Test-Path -LiteralPath $taskBackupPath)) {
    $taskBackups | ConvertTo-Json | Set-Content -LiteralPath $taskBackupPath -Encoding utf8
}
foreach ($taskBackup in $taskBackups) {
    $taskAcl = [System.Security.AccessControl.FileSecurity]::new()
    $taskAcl.SetOwner($taskIdentity.User)
    $taskAcl.SetAccessRuleProtection($true, $false)
    foreach ($taskSid in @($taskIdentity.User, [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18'), [System.Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))) {
        $taskAcl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new($taskSid, 'FullControl', 'Allow'))
    }
    Set-Acl -LiteralPath (Join-Path $taskRuntime $taskBackup.name) -AclObject $taskAcl
}
Write-Output ('Restricted private file access to the operator, SYSTEM and administrators: ' + $taskBackups.Count)

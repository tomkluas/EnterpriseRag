$ErrorActionPreference = 'Stop'
$entry = Join-Path $PSScriptRoot 'deploy.ps1'
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('v3-deploy-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temporary | Out-Null
$configuration = Join-Path $temporary '.env'
try {
    & $entry -Action Init -ConfigFile $configuration
    $initial = [IO.File]::ReadAllText($configuration)
    if ($initial -notmatch 'V3_MODE=development' -or $initial -match '__[A-Z_]+__') { throw 'Invalid generated configuration' }
    if ($initial -notmatch '(?m)^WK_ADMIN_PASSWORD=Aa1!.{24}\s*$') { throw 'Platform password does not meet policy' }
    & $entry -Action Init -ConfigFile $configuration
    if ([IO.File]::ReadAllText($configuration) -cne $initial) { throw 'Existing configuration was overwritten' }
    $global:v3TestCommands = @()
    function global:docker { $global:v3TestCommands += ,@($args); $global:LASTEXITCODE = 0 }
    & $entry -Action Up -ConfigFile $configuration
    $commandsText = ($global:v3TestCommands | ForEach-Object { $_ -join ' ' }) -join "`n"
    if ($commandsText -notmatch 'run --rm migrate' -or $commandsText -notmatch 'up -d --wait --wait-timeout 120 api worker') { throw 'Missing migration or readiness check' }
    if ($commandsText.IndexOf('run --rm migrate') -gt $commandsText.IndexOf('up -d --wait --wait-timeout 120 api worker')) { throw 'Application started before migration' }
    $global:v3TestCommands = @()
    function global:docker { $global:v3TestCommands += ,@($args); $global:LASTEXITCODE = 2 }
    $failed = $false
    try { & $entry -Action Up -ConfigFile $configuration } catch { $failed = $true }
    if (!$failed -or $global:v3TestCommands.Count -ne 1) { throw 'Command failure did not stop deployment' }
    $failed = $false
    try { & $entry -Environment Production -Action Up -ConfigFile $configuration } catch { $failed = $true }
    if (!$failed) { throw 'Production accepted development configuration' }
    [IO.File]::WriteAllText($configuration, $initial.Replace('V3_MODE=development','V3_MODE=production') + "`nV3_IMAGE=registry.example/app:v3-test`n")
    $global:v3TestCommands = @()
    function global:docker {
        $global:v3TestCommands += ,@($args)
        $global:LASTEXITCODE = if (($args -contains 'migrate') -and !($args -contains 'check-configuration')) { 2 } else { 0 }
    }
    $failed = $false
    try { & $entry -Environment Production -Action Up -Pull -ConfigFile $configuration } catch { $failed = $true }
    $commandsText = ($global:v3TestCommands | ForEach-Object { $_ -join ' ' }) -join "`n"
    if (!$failed -or $commandsText -match 'build api|up -d --wait --wait-timeout 120 api worker') { throw 'Migration failure did not prevent application start' }
    if ($commandsText -notmatch '--project-name enterpriserag-v3 ' -or $commandsText -notmatch '--profile migration pull') { throw 'Production pull/project mismatch' }
    if ($commandsText.IndexOf('stop api worker') -gt $commandsText.LastIndexOf('run --rm migrate')) { throw 'Old writers were not stopped before migration' }
    Write-Output 'PASS: generated secrets, configuration preservation, migration ordering, fail-fast and production guard.'
} finally {
    Remove-Item Function:\docker -ErrorAction SilentlyContinue
    # Only delete the test-created, explicitly scoped temporary directory.
    if ((Split-Path $temporary -Leaf) -like 'v3-deploy-test-*') { Remove-Item -LiteralPath $temporary -Recurse -Force }
}

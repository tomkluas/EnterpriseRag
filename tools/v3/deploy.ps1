# Unified, non-destructive Docker Compose entry point. PowerShell 5.1 / 7.
param(
    [ValidateSet('Development','Production')][string]$Environment = 'Development',
    [ValidateSet('Init','Up','Configure','Check','Stop','Logs')][string]$Action = 'Up',
    [string]$ConfigFile = '',
    [switch]$Pull
)
$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$directory = if ($Environment -eq 'Production') { 'deploy/v3' } else { 'deploy/dev' }
$deployment = Join-Path $repository $directory
if (!$ConfigFile) { $ConfigFile = Join-Path $deployment '.env' }
$ConfigFile = [IO.Path]::GetFullPath($ConfigFile)
if ($Action -eq 'Init') {
    if (Test-Path -LiteralPath $ConfigFile) { Write-Output 'Configuration exists; preserved.'; return }
    $template = [IO.File]::ReadAllText((Join-Path $deployment '.env.example'))
    if ($Environment -eq 'Development') {
        foreach ($name in @('DATABASE_PASSWORD','WK_DATABASE_PASSWORD','WK_JWT_SECRET','WK_AES_KEY','WK_SIGNING_KEY','WK_ADMIN_PASSWORD','BOOTSTRAP_TOKEN')) {
            $bytes = New-Object byte[] 32
            $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
            try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
            $secret = ([BitConverter]::ToString($bytes)).Replace('-','').ToLowerInvariant()
            if ($name -eq 'WK_AES_KEY') { $secret = $secret.Substring(0,32) }
            if ($name -eq 'WK_ADMIN_PASSWORD') { $secret = 'Aa1!' + $secret.Substring(0,24) }
            $template = $template.Replace('__' + $name + '__', $secret)
        }
    }
    [IO.Directory]::CreateDirectory((Split-Path $ConfigFile -Parent)) | Out-Null
    # Exclusive creation protects an existing config even if two installers run at once.
    $stream = [IO.File]::Open($ConfigFile, [IO.FileMode]::CreateNew)
    try { $bytes = [Text.UTF8Encoding]::new($false).GetBytes($template); $stream.Write($bytes,0,$bytes.Length) } finally { $stream.Dispose() }
    Write-Output "Created $ConfigFile. Edit model / production settings before deployment."
    return
}
if (!(Test-Path -LiteralPath $ConfigFile)) { throw 'Configuration missing. Run -Action Init first.' }
$configText = [IO.File]::ReadAllText($ConfigFile)
$expectedMode = if ($Environment -eq 'Production') { 'production' } else { 'development' }
if ($configText -notmatch ('(?m)^V3_MODE=' + $expectedMode + '\s*$')) { throw "Expected V3_MODE=$expectedMode in configuration." }
if (!(Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker with Compose v2 is required.' }
$project = if ($Environment -eq 'Production') { 'enterpriserag-v3' } else { 'enterpriserag-v3-development' }
$compose = @('compose','--project-name',$project,'--env-file',$ConfigFile,'-f',(Join-Path $deployment 'compose.yaml'))
# Alternate config paths are also used by env_file and read-only CLI mounts.
$previousConfig = $env:V3_COMPOSE_ENV_FILE
$previousSetupDirectory = $env:V3_SETUP_DIRECTORY
$previousSetupConfig = $env:V3_SETUP_CONFIG
$env:V3_COMPOSE_ENV_FILE = $ConfigFile
$env:V3_SETUP_DIRECTORY = Split-Path $ConfigFile -Parent
$env:V3_SETUP_CONFIG = '/setup/' + (Split-Path $ConfigFile -Leaf)
function Invoke-Compose([string[]]$Arguments) {
    & docker @compose @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose failed. Check the command output; deployment stopped.' }
}
try {
    Invoke-Compose @('config','--quiet')
    switch ($Action) {
        'Configure' {
            if ($Environment -ne 'Development') { throw 'Configure is development-only.' }
            Invoke-Compose @('--profile','setup','run','--rm','--no-deps','configure')
            Write-Output 'Restart api/worker with Up to load generated credentials. Follow quick-start.md for validation.'
        }
        'Stop' { Invoke-Compose @('stop') }
        'Logs' { Invoke-Compose @('logs','--tail','100') }
        'Check' {
            Invoke-Compose @('ps','--all')
            Invoke-Compose @('--profile','migration','run','--rm','--no-deps','migrate','python','-m','app.v3.cli','--env-file','/run/v3.env','check-configuration')
        }
        'Up' {
            # Pull mode requires an explicitly versioned prebuilt application image.
            if ($Pull) {
                if ($configText -notmatch '(?m)^V3_IMAGE=\S+\s*$' -or $configText -match '(?m)^V3_IMAGE=.*:local\s*$') { throw 'Set V3_IMAGE to a published version before using -Pull.' }
                Invoke-Compose @('--profile','migration','pull')
            } else { Invoke-Compose @('build','api') }
            Invoke-Compose @('--profile','migration','run','--rm','--no-deps','migrate','python','-m','app.v3.cli','--env-file','/run/v3.env','check-configuration')
            if ($Environment -eq 'Development') { Invoke-Compose @('run','--rm','--no-deps','objects-init') }
            $dependencies = if ($Environment -eq 'Production') { @('database') } else { @('database','wk-database','wk-redis','docreader','weknora','weknora-web') }
            Invoke-Compose (@('up','-d','--wait','--wait-timeout','240') + $dependencies)
            # Stop old writers before a production schema migration. Operators must
            # already have taken the documented backup and maintenance window.
            if ($Environment -eq 'Production') { Invoke-Compose @('stop','api','worker') }
            Invoke-Compose @('--profile','migration','run','--rm','migrate')
            Invoke-Compose @('up','-d','--wait','--wait-timeout','120','api','worker')
            Write-Output 'Services started. This is infrastructure readiness, not platform/model acceptance.'
            if ($Environment -eq 'Development') { Write-Output 'Workbench: http://127.0.0.1:19000 ; WeKnora: http://127.0.0.1:19081 ; next: docs/v3/quick-start.md' }
        }
    }
} finally {
    $env:V3_COMPOSE_ENV_FILE = $previousConfig
    $env:V3_SETUP_DIRECTORY = $previousSetupDirectory
    $env:V3_SETUP_CONFIG = $previousSetupConfig
}

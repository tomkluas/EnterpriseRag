# 桌面启动入口依赖已准备的本地配置、Python 环境和 Docker 容器；会启动服务并检查就绪端点，不负责首次部署。
param([switch]$NoPause, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
Set-Location -LiteralPath $repository

# 只检查 Docker 引擎是否能响应；桌面进程存在并不表示引擎已经就绪。
function Test-Docker {
    # Windows PowerShell 5 treats native stderr as an error record.
    $ErrorActionPreference = 'Continue'
    & docker info --format '{{.ServerVersion}}' *> $null
    return $LASTEXITCODE -eq 0
}
# 将网络失败与非 200 统一视为尚未就绪，供有限时长的重试使用。
function Test-Endpoint([string]$Url) {
    try { return (Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 }
    catch { return $false }
}
# 给服务有限的启动窗口；超时即失败并提示日志位置，避免无限等待掩盖启动错误。
function Wait-Endpoint([string]$Url, [int]$Seconds = 60) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        if (Test-Endpoint $Url) { return }
        Start-Sleep -Seconds 1
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Service did not become ready: $Url. Check data-v3/logs and Docker Desktop."
}
# 只启动已有容器；容器缺失说明本地部署尚未准备，不能在此静默创建另一套环境。
function Start-Container([string]$Name) {
    $running = & docker inspect --format '{{.State.Running}}' $Name 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Required container not found: $Name. Existing local deployment is required." }
    if ($running -ne 'true') {
        & docker start $Name | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "Could not start container: $Name" }
    }
}

try {
    if (!(Test-Path 'v3.local') -or !(Test-Path 'backend/.venv-v3/Scripts/python.exe')) {
        throw 'Missing v3.local or backend/.venv-v3. Follow the local setup in README.md first.'
    }
    if (!(Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker Desktop is not installed or docker is not on PATH.' }
    Write-Host '[1/4] Checking Docker Desktop...'
    if (!(Test-Docker)) {
        $desktop = Join-Path $env:ProgramFiles 'Docker/Docker/Docker Desktop.exe'
        if (!(Test-Path -LiteralPath $desktop)) { throw 'Please start Docker Desktop and try again.' }
        Start-Process -FilePath $desktop -WindowStyle Hidden
        $deadline = [DateTime]::UtcNow.AddSeconds(120)
        $dockerReady = $false
        do {
            Start-Sleep -Seconds 2
            $dockerReady = Test-Docker
            if ($dockerReady) { break }
        } while ([DateTime]::UtcNow -lt $deadline)
        if (!$dockerReady) { throw 'Docker Desktop is not ready. Open Docker Desktop and try again.' }
    }
    Write-Host '[2/4] Starting database and WeKnora...'
    foreach ($name in @('enterpriserag-v3-postgres','WeKnora-postgres','WeKnora-redis','WeKnora-docreader','WeKnora-app','WeKnora-frontend')) {
        Start-Container $name
    }
    Wait-Endpoint 'http://127.0.0.1:18080/health'
    Wait-Endpoint 'http://127.0.0.1:18081/'
    Write-Host '[3/4] Checking frontend, API and worker...'
    if (!(Test-Path 'frontend/dist/index.html')) {
        & npm.cmd --prefix frontend run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed. Run npm ci in frontend first.' }
    }
    # 校验记录的 PID 及命令行，避免 PID 被系统复用后将其他进程误当作本项目服务。
    $processesReady = $false
    if (Test-Path 'data-v3/processes.json') {
        $state = Get-Content 'data-v3/processes.json' -Raw | ConvertFrom-Json
        $processesReady = $true
        foreach ($role in @('api','worker')) {
            $processId = [int]$state.$role
            $process = Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue
            $expected = if ($role -eq 'api') { 'app\.main:app' } else { 'app\.v3\.cli.*worker' }
            if (!$process -or $process.Name -notmatch '^python(w)?\.exe$' -or $process.CommandLine -notmatch $expected) { $processesReady = $false }
        }
    }
    if (!$processesReady) { & (Join-Path $PSScriptRoot 'start_local.ps1') -Restart }
    Wait-Endpoint 'http://127.0.0.1:18000/health/ready'
    Wait-Endpoint 'http://127.0.0.1:18000/'
    Write-Host '[4/4] Ready: http://127.0.0.1:18000/' -ForegroundColor Green
    Write-Host 'WeKnora: http://127.0.0.1:18081/  Logs: data-v3/logs/'
    if (!$NoBrowser) { Start-Process 'http://127.0.0.1:18000/' }
    exit 0
} catch {
    Write-Host ('Startup failed: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}

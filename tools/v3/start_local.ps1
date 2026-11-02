# 启动本地 API 和 worker，依赖既有 v3.local 与虚拟环境；重启会终止状态文件记录且身份核验通过的进程树。
param([switch]$Restart)
$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$configuration = Join-Path $repository 'v3.local'
$python = Join-Path $repository 'backend/.venv-v3/Scripts/python.exe'
$statePath = Join-Path $repository 'data-v3/processes.json'
$logs = Join-Path $repository 'data-v3/logs'
if (!(Test-Path -LiteralPath $configuration) -or !(Test-Path -LiteralPath $python)) { throw 'Prepare v3.local and backend/.venv-v3 first.' }
# PID 可被系统复用，因此停止前必须同时检查 Python 进程名与本项目命令行特征。
if (Test-Path -LiteralPath $statePath) {
    $previous = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    foreach ($role in @('api', 'worker')) {
        $processId = [int]$previous.$role
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue
        if (!$process) { continue }
        $expected = if ($role -eq 'api') { 'app\.main:app' } else { 'app\.v3\.cli.*worker' }
        if ($process.Name -notmatch '^python(w)?\.exe$' -or $process.CommandLine -notmatch $expected) { throw "Stored $role PID belongs to another process; refusing to stop it." }
        if (!$Restart) { throw 'V3 is already running. Use -Restart to reload configuration.' }
        & taskkill /PID $processId /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Failed to stop V3 $role process tree." }
    }
}
New-Item -ItemType Directory -Path $logs -Force | Out-Null
# 两个子进程显式共用同一配置和工作目录，分别写日志；状态文件只保存 PID 与时间以供下次核验。
$env:PYTHONPATH = Join-Path $repository 'backend'
$env:V3_ENV_FILE = $configuration
$api = Start-Process -FilePath $python -ArgumentList @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port','18000') -WorkingDirectory $repository -WindowStyle Hidden -RedirectStandardOutput "$logs/api.out.log" -RedirectStandardError "$logs/api.err.log" -PassThru
$worker = Start-Process -FilePath $python -ArgumentList @('-m','app.v3.cli','--env-file',('"'+$configuration+'"'),'worker') -WorkingDirectory $repository -WindowStyle Hidden -RedirectStandardOutput "$logs/worker.out.log" -RedirectStandardError "$logs/worker.err.log" -PassThru
@{ api=$api.Id; worker=$worker.Id; started_utc=[DateTime]::UtcNow.ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
Write-Output "V3 API $($api.Id), worker $($worker.Id). Open http://127.0.0.1:18000/"

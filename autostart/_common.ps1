#Requires -Version 5.1
<#
    _common.ps1 - 共享工具函数（被本目录下其它脚本 dot-source）

    提供：读取设置、日志、健康检查、解释器解析、进程启动/停止。
#>

function Get-Pdf2zhSettings {
    <#  读取 settings.json，缺失项用默认值补齐  #>
    param([string]$Path)

    $defaults = [ordered]@{
        port                      = 8890
        serverDir                 = ''
        interpreter               = 'auto'    # auto | venv | uv
        pythonExe                 = ''
        uvExe                     = ''
        checkUpdateOnStart        = $false
        stopServerWhenZoteroExits = $true
        zoteroExitGraceSeconds    = 120
        pollSeconds               = 5
        serverStartTimeoutSeconds = 180
        startRetrySeconds         = 60
        logRetentionDays          = 7
    }

    $cfg = [ordered]@{}
    foreach ($k in $defaults.Keys) { $cfg[$k] = $defaults[$k] }

    if ($Path -and (Test-Path -LiteralPath $Path)) {
        try {
            # 显式按 UTF-8 读取并去掉可能存在的 BOM，
            # 避免中文注释导致不同编码下解析失败。
            $utf8 = New-Object System.Text.UTF8Encoding($false)
            $raw = $utf8.GetString([System.IO.File]::ReadAllBytes($Path))
            while ($raw.Length -gt 0 -and [int][char]$raw[0] -eq 0xFEFF) { $raw = $raw.Substring(1) }
            $parsed = $raw | ConvertFrom-Json
            foreach ($p in $parsed.PSObject.Properties) {
                if ($cfg.Contains($p.Name)) { $cfg[$p.Name] = $p.Value }
            }
        } catch {
            # 设置文件损坏时静默使用默认值
        }
    }
    return [pscustomobject]$cfg
}

function Get-Pdf2zhPaths {
    <#  计算 autostart / server / logs 的绝对路径  #>
    param([string]$AutostartDir, $Settings)

    if (-not $AutostartDir) { $AutostartDir = $PSScriptRoot }
    $AutostartDir = (Resolve-Path -LiteralPath $AutostartDir).Path

    $serverDir = [string]$Settings.serverDir
    if (-not $serverDir) {
        $serverDir = Join-Path (Split-Path -Parent $AutostartDir) 'server'
    }
    $logDir = Join-Path $AutostartDir 'logs'
    if (-not (Test-Path -LiteralPath $logDir)) {
        New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    }
    return [pscustomobject]@{
        AutostartDir = $AutostartDir
        ServerDir    = $serverDir
        LogDir       = $logDir
        LauncherLog  = Join-Path $logDir 'autostart.log'
        StateFile    = Join-Path $logDir 'server.state.json'
    }
}

function Write-Pdf2zhLog {
    <#  追加一行日志（带时间戳），失败时不抛异常  #>
    param(
        [Parameter(Mandatory = $true)][string]$LogFile,
        [Parameter(Mandatory = $true)][string]$Message,
        [ValidateSet('INFO', 'WARN', 'ERROR')][string]$Level = 'INFO'
    )
    try {
        $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
        Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8 -ErrorAction Stop
    } catch { }
}

function Test-Pdf2zhHealth {
    <#  访问 /health，健康则返回响应对象，否则返回 $null  #>
    param([int]$Port = 8890, [int]$TimeoutSec = 3)

    try {
        $resp = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/health" -f $Port) -TimeoutSec $TimeoutSec -UseBasicParsing -ErrorAction Stop
        if ($resp.StatusCode -ne 200) { return $null }
        $data = $resp.Content | ConvertFrom-Json -ErrorAction Stop
        if ($data.status -eq 'ok' -or $data.version) { return $data }
    } catch { }
    return $null
}

function Get-Pdf2zhServerState {
    <#  读取上次启动记录（pid 等）  #>
    param([string]$StateFile)
    if (Test-Path -LiteralPath $StateFile) {
        try { return Get-Content -LiteralPath $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json } catch { }
    }
    return $null
}

function Save-Pdf2zhServerState {
    param([string]$StateFile, $State)
    try {
        ($State | ConvertTo-Json -Depth 4) | Set-Content -LiteralPath $StateFile -Encoding UTF8 -ErrorAction Stop
    } catch { }
}

function Test-Pdf2zhServerProcess {
    <#  判断记录的 pid 是否仍是本目录下的 server.py 进程  #>
    param([string]$StateFile, [string]$ServerDir)

    $state = Get-Pdf2zhServerState -StateFile $StateFile
    if (-not $state -or -not $state.pid) { return $null }

    $proc = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f [int]$state.pid) -ErrorAction SilentlyContinue
    if (-not $proc) { return $null }
    if ($proc.Name -notmatch '^python') { return $null }
    if ($proc.CommandLine -notmatch 'server\.py') { return $null }
    return $state
}

function Resolve-Pdf2zhInterpreter {
    <#  决定用哪个解释器启动 server.py
        - venv : 直接用托管虚拟环境的 python（快、离线可用）
        - uv   : uv run --python 3.12 --with-requirements requirements.txt server.py
    #>
    param($Settings, [string]$ServerDir)

    $mode = [string]$Settings.interpreter
    if ($Settings.pythonExe -and (Test-Path -LiteralPath $Settings.pythonExe)) {
        return [pscustomobject]@{ Exe = $Settings.pythonExe; Args = @('server.py'); Kind = 'explicit' }
    }

    if ($mode -ne 'uv') {
        $candidates = @(
            (Join-Path $ServerDir 'zotero-pdf2zh-next-venv\Scripts\python.exe'),
            (Join-Path $ServerDir 'zotero-pdf2zh-venv\Scripts\python.exe')
        )
        foreach ($cand in $candidates) {
            if (-not (Test-Path -LiteralPath $cand)) { continue }
            # 确认该环境具备 server.py 自身依赖，避免起不来
            $probe = 'import flask, toml, pypdf, fitz, packaging'
            try {
                $null = & $cand -c $probe 2>&1
                if ($LASTEXITCODE -eq 0) {
                    return [pscustomobject]@{ Exe = $cand; Args = @('server.py'); Kind = 'venv' }
                }
            } catch { }
        }
        if ($mode -eq 'venv') {
            throw "未找到可用的托管虚拟环境 python（interpreter=venv）"
        }
    }

    $uv = [string]$Settings.uvExe
    if (-not $uv -or -not (Test-Path -LiteralPath $uv)) {
        $cmd = Get-Command uv -ErrorAction SilentlyContinue
        if ($cmd) { $uv = $cmd.Source }
    }
    if ($uv -and (Test-Path -LiteralPath $uv)) {
        return [pscustomobject]@{
            Exe  = $uv
            Args = @('run', '--python', '3.12', '--with-requirements', 'requirements.txt', 'server.py')
            Kind = 'uv'
        }
    }

    throw "找不到可用的解释器：既没有托管虚拟环境，也没有 uv。请检查 $ServerDir"
}

function Start-Pdf2zhServerProcess {
    <#  后台隐藏启动 server.py，返回进程对象；日志写入 logs\server-<日期>.log  #>
    param($Settings, $Paths, [switch]$Force)

    $port = [int]$Settings.port
    $interp = Resolve-Pdf2zhInterpreter -Settings $Settings -ServerDir $Paths.ServerDir

    $stamp = Get-Date -Format 'yyyyMMdd'
    $outLog = Join-Path $Paths.LogDir ("server-{0}.out.log" -f $stamp)
    $errLog = Join-Path $Paths.LogDir ("server-{0}.err.log" -f $stamp)

    # 空文件作为 stdin：server.py 里未被 try 包住的 input() 会立刻 EOF 而不是挂起
    $stdinFile = Join-Path $Paths.LogDir 'stdin-empty.txt'
    if (-not (Test-Path -LiteralPath $stdinFile)) {
        Set-Content -LiteralPath $stdinFile -Value '' -Encoding UTF8 -NoNewline
    }

    $argList = @($interp.Args) + @(
        '--port', "$port",
        '--check_update', ($(if ($Settings.checkUpdateOnStart) { 'true' } else { 'false' }))
    )

    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUTF8 = '1'

    $proc = Start-Process -FilePath $interp.Exe -ArgumentList $argList -WorkingDirectory $Paths.ServerDir -WindowStyle Hidden -RedirectStandardInput $stdinFile -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru -ErrorAction Stop

    Save-Pdf2zhServerState -StateFile $Paths.StateFile -State ([ordered]@{
        pid       = $proc.Id
        port      = $port
        kind      = $interp.Kind
        exe       = $interp.Exe
        startedAt = (Get-Date -Format 'o')
    })

    return [pscustomobject]@{ Process = $proc; Interp = $interp; OutLog = $outLog; ErrLog = $errLog }
}

function Stop-Pdf2zhServerProcess {
    <#  停止由本工具启动的 server.py（按记录的 pid，必要时按命令行兜底）  #>
    param($Paths, [int]$Port = 8890, [switch]$Quiet)

    $killed = @()
    $state = Test-Pdf2zhServerProcess -StateFile $Paths.StateFile -ServerDir $Paths.ServerDir
    if ($state) {
        try {
            Stop-Process -Id ([int]$state.pid) -Force -ErrorAction Stop
            $killed += [int]$state.pid
        } catch { }
    }

    # 兜底：命令行里带 server.py 且端口一致的 python 进程
    $procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -match '^python' -and
            $_.CommandLine -match 'server\.py' -and
            $_.CommandLine -match ("--port\s+{0}" -f $Port)
        }
    foreach ($p in $procs) {
        if ($killed -contains [int]$p.ProcessId) { continue }
        try {
            Stop-Process -Id ([int]$p.ProcessId) -Force -ErrorAction Stop
            $killed += [int]$p.ProcessId
        } catch { }
    }

    if (Test-Path -LiteralPath $Paths.StateFile) {
        Remove-Item -LiteralPath $Paths.StateFile -Force -ErrorAction SilentlyContinue
    }
    return $killed
}

function Remove-Pdf2zhOldLogs {
    <#  清理过期日志  #>
    param($Paths, [int]$RetentionDays = 7)
    if ($RetentionDays -le 0) { return }
    $cutoff = (Get-Date).AddDays(-$RetentionDays)
    try {
        Get-ChildItem -LiteralPath $Paths.LogDir -Filter 'server-*.log' -File -ErrorAction SilentlyContinue |
            Where-Object { $_.LastWriteTime -lt $cutoff } |
            Remove-Item -Force -ErrorAction SilentlyContinue
    } catch { }
}

#Requires -Version 5.1
<#
    zotero-pdf2zh-autostart.ps1

    常驻守护进程：
      1. 监听 Zotero 是否在运行；
      2. Zotero 一启动，就自动在后台（无窗口）拉起 PDF2zh Server；
      3. Server 意外退出时自动重启；
      4. Zotero 退出后（超过宽限期）自动停掉 Server，释放资源。

    本脚本由“启动文件夹快捷方式”或“计划任务”在登录时自动运行，
    因此用户不需要再手动开终端执行 uv run ... server.py。

    用法：
      powershell -NoProfile -ExecutionPolicy Bypass -File zotero-pdf2zh-autostart.ps1
      powershell ... -File zotero-pdf2zh-autostart.ps1 -Once      # 只跑一轮，便于排查
#>
[CmdletBinding()]
param(
    [switch]$Once,          # 只执行一轮检查后退出（调试用）
    [switch]$Verbose2       # 输出更多信息到控制台（调试用）
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot '_common.ps1')

# ---------- 单实例互斥：避免开机自启 + 手动运行同时存在两份守护 ----------
$mutexName = 'Local\ZoteroPdf2zhAutostart'
$mutex = New-Object System.Threading.Mutex($false, $mutexName)
$hasLock = $false
try { $hasLock = $mutex.WaitOne(0, $false) } catch [System.Threading.AbandonedMutexException] { $hasLock = $true }
if (-not $hasLock) {
    # 已经有一份守护在跑，直接退出（不报错）
    exit 0
}

# ---------- 初始化 ----------
$settings = Get-Pdf2zhSettings -Path (Join-Path $PSScriptRoot 'settings.json')
$paths    = Get-Pdf2zhPaths -AutostartDir $PSScriptRoot -Settings $settings

function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    Write-Pdf2zhLog -LogFile $paths.LauncherLog -Message $Message -Level $Level
    if ($Verbose2) { Write-Host ("[{0}] {1}" -f $Level, $Message) }
}

function Test-ZoteroRunning {
    <#  Zotero 主进程是否在运行  #>
    $p = Get-Process -Name 'zotero' -ErrorAction SilentlyContinue
    return [bool]$p
}

function Wait-Pdf2zhHealthy {
    <#  等待 Server 通过健康检查  #>
    param([int]$Port, [int]$TimeoutSeconds)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $health = Test-Pdf2zhHealth -Port $Port -TimeoutSec 3
        if ($health) { return $health }
        Start-Sleep -Seconds 2
    }
    return $null
}

function Ensure-Pdf2zhServer {
    <#  确保 Server 在跑；返回 $true 表示健康  #>
    param([int]$Port)

    $health = Test-Pdf2zhHealth -Port $Port -TimeoutSec 3
    if ($health) { return $true }

    # 启动冷却：避免反复拉起失败进程（例如配置错误、端口被占）
    $retryFile = Join-Path $paths.LogDir 'last-start-attempt.txt'
    if (Test-Path -LiteralPath $retryFile) {
        try {
            $last = [datetime]::Parse((Get-Content -LiteralPath $retryFile -Raw).Trim())
            $elapsed = [int]((Get-Date) - $last).TotalSeconds
            $wait = [int]$settings.startRetrySeconds - $elapsed
            if ($wait -gt 0) {
                Write-Log ("Server 尚未就绪（距上次启动尝试 {0}s），{1}s 后重试" -f $elapsed, $wait) 'WARN'
                return $false
            }
        } catch { }
    }
    Set-Content -LiteralPath $retryFile -Value (Get-Date -Format 'o') -Encoding UTF8

    Write-Log '检测到 Zotero 已启动，正在后台启动 PDF2zh Server ...'
    try {
        $started = Start-Pdf2zhServerProcess -Settings $settings -Paths $paths
        Write-Log ("已拉起 Server 进程 pid={0}，解释器={1}" -f $started.Process.Id, $started.Interp.Kind)
    } catch {
        Write-Log ("启动 Server 失败: {0}" -f $_.Exception.Message) 'ERROR'
        return $false
    }

    $health = Wait-Pdf2zhHealthy -Port $Port -TimeoutSeconds ([int]$settings.serverStartTimeoutSeconds)
    if ($health) {
        Write-Log ("Server 就绪: version={0} port={1}" -f $health.version, $Port)
        Remove-Item -LiteralPath $retryFile -Force -ErrorAction SilentlyContinue
        Remove-Pdf2zhOldLogs -Paths $paths -RetentionDays ([int]$settings.logRetentionDays)
        return $true
    }

    Write-Log ("Server 启动超时（{0}s 内未通过 /health）" -f $settings.serverStartTimeoutSeconds) 'ERROR'
    Write-Log ("  请查看日志排查: {0}" -f $started.OutLog) 'ERROR'
    Write-Log ("                  {0}" -f $started.ErrLog) 'ERROR'
    return $false
}

function Stop-Pdf2zhServerIfNeeded {
    <#  Zotero 已退出，按需停掉 Server  #>
    param([int]$Port)
    $killed = Stop-Pdf2zhServerProcess -Paths $paths -Port $Port -Quiet
    if ($killed.Count -gt 0) {
        Write-Log ("Zotero 已退出，已停止 Server (pid: {0})" -f ($killed -join ', '))
    }
}

# ---------- 主循环 ----------
$port            = [int]$settings.port
$pollSeconds     = [Math]::Max(1, [int]$settings.pollSeconds)
$graceSeconds    = [Math]::Max(0, [int]$settings.zoteroExitGraceSeconds)
$stopWithZotero  = [bool]$settings.stopServerWhenZoteroExits

$zoteroSeen      = $false
$zoteroExitedAt  = $null
$wasHealthy      = $false

Write-Log ("守护已启动 (pid={0}, 端口={1}, 轮询={2}s)" -f $PID, $port, $pollSeconds)

try {
    while ($true) {
        $zoteroUp = Test-ZoteroRunning

        if ($zoteroUp) {
            $zoteroExitedAt = $null
            if (-not $zoteroSeen) {
                Write-Log '检测到 Zotero 正在运行'
                $zoteroSeen = $true
            }

            $healthy = Ensure-Pdf2zhServer -Port $port
            if ($healthy -and -not $wasHealthy) {
                Write-Log '翻译服务已可用（在 Zotero 中可直接右键翻译）'
            } elseif (-not $healthy -and $wasHealthy) {
                Write-Log 'Server 健康检查失败，将尝试重新拉起' 'WARN'
            }
            $wasHealthy = $healthy
        }
        else {
            if ($zoteroSeen) {
                if (-not $zoteroExitedAt) {
                    $zoteroExitedAt = Get-Date
                    Write-Log 'Zotero 已退出'
                }
                $elapsed = ((Get-Date) - $zoteroExitedAt).TotalSeconds
                if ($stopWithZotero -and $elapsed -ge $graceSeconds) {
                    Stop-Pdf2zhServerIfNeeded -Port $port
                    $zoteroSeen     = $false
                    $zoteroExitedAt = $null
                    $wasHealthy     = $false
                }
            }
        }

        if ($Once) { break }
        Start-Sleep -Seconds $pollSeconds
    }
} finally {
    if ($hasLock) { try { $mutex.ReleaseMutex() } catch { } }
    $mutex.Dispose()
}

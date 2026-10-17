#Requires -Version 5.1
<#
    pdf2zh-autostart.ctl.ps1 —— 自动启动的安装 / 卸载 / 状态 / 控制台

    常用命令（在本目录下运行）：
      powershell -NoProfile -ExecutionPolicy Bypass -File pdf2zh-autostart.ctl.ps1 status
      powershell ... -File pdf2zh-autostart.ctl.ps1 install
      powershell ... -File pdf2zh-autostart.ctl.ps1 uninstall
      powershell ... -File pdf2zh-autostart.ctl.ps1 start | stop | restart
      powershell ... -File pdf2zh-autostart.ctl.ps1 log
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'uninstall', 'status', 'start', 'stop', 'restart', 'log', 'test')]
    [string]$Action = 'status',

    [int]$TailLines = 40
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

$settings = Get-Pdf2zhSettings -Path (Join-Path $PSScriptRoot 'settings.json')
$paths    = Get-Pdf2zhPaths -AutostartDir $PSScriptRoot -Settings $settings
$port     = [int]$settings.port

$TaskName    = 'Zotero PDF2zh Autostart'
$StartupDir  = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
$ShortcutPath = Join-Path $StartupDir 'Zotero PDF2zh Autostart.lnk'
$Supervisor  = Join-Path $PSScriptRoot 'zotero-pdf2zh-autostart.ps1'

function Get-SupervisorProcess {
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match 'zotero-pdf2zh-autostart\.ps1' }
}

function Show-Status {
    Write-Host ''
    Write-Host '=== Zotero PDF2zh 自动启动 · 状态 ===' -ForegroundColor Cyan
    Write-Host ("守护脚本   : {0}" -f $Supervisor)
    Write-Host ("Server 目录: {0}" -f $paths.ServerDir)
    Write-Host ("端口       : {0}" -f $port)

    $sup = @(Get-SupervisorProcess)
    if ($sup.Count -gt 0) {
        Write-Host ("守护进程   : 运行中 (pid {0})" -f (($sup | ForEach-Object { $_.ProcessId }) -join ', ')) -ForegroundColor Green
    } else {
        Write-Host '守护进程   : 未运行' -ForegroundColor Yellow
    }

    $health = Test-Pdf2zhHealth -Port $port -TimeoutSec 3
    if ($health) {
        Write-Host ("翻译服务   : 正常 (version {0})  http://127.0.0.1:{1}/health" -f $health.version, $port) -ForegroundColor Green
    } else {
        Write-Host '翻译服务   : 未响应' -ForegroundColor Yellow
    }

    if (Get-Process -Name 'zotero' -ErrorAction SilentlyContinue) {
        Write-Host 'Zotero     : 正在运行' -ForegroundColor Green
    } else {
        Write-Host 'Zotero     : 未运行'
    }

    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) { Write-Host ("计划任务   : 已注册 ({0})" -f $task.State) -ForegroundColor Green }
    else { Write-Host '计划任务   : 未注册' }

    if (Test-Path -LiteralPath $ShortcutPath) { Write-Host '启动快捷方式: 已创建' -ForegroundColor Green }
    else { Write-Host '启动快捷方式: 未创建' }

    Write-Host ("日志文件   : {0}" -f $paths.LauncherLog)
    Write-Host ''
}

function Install-Autostart {
    Write-Host '正在注册开机自启 ...' -ForegroundColor Cyan

    # 1) 计划任务（登录时触发，无窗口）
    $psExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $argLine = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $Supervisor
    $action  = New-ScheduledTaskAction -Execute $psExe -Argument $argLine -WorkingDirectory $PSScriptRoot
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    # 让任务每 5 分钟自检一次：万一守护进程意外退出，最多 5 分钟内自动恢复。
    # 守护进程仍在运行时，MultipleInstances=IgnoreNew 保证不会重复启动。
    try {
        $rep = (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)).Repetition
        $trigger.Repetition = $rep
    } catch { }
    $set     = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -DontStopOnIdleEnd -RunOnlyIfIdle:$false -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
    $principal = New-ScheduledTaskPrincipal -UserId ("{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME) -LogonType Interactive -RunLevel Limited

    try {
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $set -Principal $principal -Force -ErrorAction Stop | Out-Null
        Write-Host '  [OK] 计划任务已注册（登录时自动启动）' -ForegroundColor Green
    } catch {
        Write-Host ("  [!!] 计划任务注册失败: {0}" -f $_.Exception.Message) -ForegroundColor Yellow
        Write-Host '       将只使用“启动文件夹”方式。' -ForegroundColor Yellow
    }

    # 2) 启动文件夹快捷方式（双保险；与计划任务共存，靠互斥锁避免重复）
    try {
        if (-not (Test-Path -LiteralPath $StartupDir)) { New-Item -ItemType Directory -Force -Path $StartupDir | Out-Null }
        $sh = New-Object -ComObject WScript.Shell
        $lnk = $sh.CreateShortcut($ShortcutPath)
        $lnk.TargetPath       = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $lnk.Arguments        = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $Supervisor
        $lnk.WorkingDirectory = $PSScriptRoot
        $lnk.WindowStyle      = 7
        $lnk.Description      = 'Zotero PDF2zh 翻译服务自动启动'
        $lnk.Save()
        Write-Host '  [OK] 启动文件夹快捷方式已创建' -ForegroundColor Green
    } catch {
        Write-Host ("  [!!] 创建快捷方式失败: {0}" -f $_.Exception.Message) -ForegroundColor Yellow
    }

    Start-Autostart
    Write-Host ''
    Write-Host '安装完成：以后打开 Zotero 会自动启动翻译服务，无需再手动开终端。' -ForegroundColor Green
}

function Uninstall-Autostart {
    Write-Host '正在取消开机自启 ...' -ForegroundColor Cyan

    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Write-Host '  [OK] 计划任务已删除' -ForegroundColor Green
    }
    if (Test-Path -LiteralPath $ShortcutPath) {
        Remove-Item -LiteralPath $ShortcutPath -Force -ErrorAction SilentlyContinue
        Write-Host '  [OK] 启动快捷方式已删除' -ForegroundColor Green
    }

    Stop-Autostart
    Write-Host '已取消自启（翻译服务已停止）。' -ForegroundColor Green
}

function Start-Autostart {
    if (@(Get-SupervisorProcess).Count -gt 0) {
        Write-Host '守护进程已在运行，跳过启动。'
        return
    }
    $psExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    Start-Process -FilePath $psExe -ArgumentList @('-NoProfile', '-WindowStyle', 'Hidden', '-ExecutionPolicy', 'Bypass', '-File', $Supervisor) -WindowStyle Hidden
    Write-Host '守护进程已启动。' -ForegroundColor Green
}

function Stop-Autostart {
    $sup = @(Get-SupervisorProcess)
    foreach ($p in $sup) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if ($sup.Count -gt 0) { Write-Host ("守护进程已停止 (pid {0})" -f (($sup | ForEach-Object { $_.ProcessId }) -join ', ')) -ForegroundColor Green }

    $killed = Stop-Pdf2zhServerProcess -Paths $paths -Port $port
    if ($killed.Count -gt 0) { Write-Host ("翻译服务已停止 (pid {0})" -f ($killed -join ', ')) -ForegroundColor Green }
}

switch ($Action) {
    'install'   { Install-Autostart }
    'uninstall' { Uninstall-Autostart }
    'status'    { Show-Status }
    'start'     { Start-Autostart }
    'stop'      { Stop-Autostart }
    'restart'   { Stop-Autostart; Start-Sleep -Seconds 2; Start-Autostart }
    'log'       { Get-Content -LiteralPath $paths.LauncherLog -Tail $TailLines -ErrorAction SilentlyContinue }
    'test'      {
        Write-Host '执行一轮检查（-Once）...' -ForegroundColor Cyan
        & (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -NoProfile -ExecutionPolicy Bypass -File $Supervisor -Once -Verbose2
    }
}

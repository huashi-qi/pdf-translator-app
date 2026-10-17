# -*- coding: utf-8 -*-
# sync-and-push.ps1 — 把 app\ 的改动同步到 src\ 并推送到 GitHub
#
# 用法：
#   .\sync-and-push.ps1                       # 自动生成提交信息
#   .\sync-and-push.ps1 -Message "修复 xxx"    # 自定义提交信息
#   .\sync-and-push.ps1 -Proxy               # 本机直连不通时走代理
#
# 说明：
#   app\ 是你的开发目录（有 config.json / cache / vendor）
#   src\ 是仓库里只放源码的目录
#   本脚本把 app\ 的 .py/.md/.vbs/.bat/.csv/.ps1 同步到 src\，然后提交推送

param(
    [string]$Message = "",
    [switch]$Proxy,
    [string]$ProxyUrl = "http://127.0.0.1:7897",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Say($t, $c = "Gray") { Write-Host $t -ForegroundColor $c }

# ---------- 1. 同步 app -> src ----------
Say ""; Say "== 1/4  同步源码 ==" "Cyan"
$exts = @(".py", ".md", ".vbs", ".bat", ".csv", ".ps1", ".json")
$skip = @("config.json", "history.json")     # 含个人配置，不入库
$copied = 0

Get-ChildItem (Join-Path $root "app") -File -ErrorAction SilentlyContinue | Where-Object {
    $exts -contains $_.Extension -and $skip -notcontains $_.Name
} | ForEach-Object {
    $dst = Join-Path (Join-Path $root "src") $_.Name
    $need = $true
    if (Test-Path $dst) {
        $need = (Get-FileHash $_.FullName).Hash -ne (Get-FileHash $dst).Hash
    }
    if ($need) {
        if (-not $DryRun) { Copy-Item $_.FullName $dst -Force }
        Say ("  + " + $_.Name) "Green"
        $copied++
    }
}
if ($copied -eq 0) { Say "  （源码无变化）" "DarkGray" }

# ---------- 2. 检查仓库体积 ----------
Say ""; Say "== 2/4  体积检查 ==" "Cyan"
$huge = Get-ChildItem $root -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Length -gt 50MB -and $_.FullName -notmatch "\\(runtime|release|build|server|cache|vendor)\\" }
if ($huge) {
    Say "  ⚠ 发现大文件（GitHub 单文件上限 100 MB）：" "Yellow"
    $huge | ForEach-Object { Say ("    " + $_.FullName.Replace($root, "") + "  " + [Math]::Round($_.Length/1MB,1) + " MB") "Yellow" }
    Say "  请确认它们已被 .gitignore 排除" "Yellow"
} else {
    Say "  OK 无超大文件" "Green"
}

# ---------- 3. 提交 ----------
Say ""; Say "== 3/4  提交 ==" "Cyan"
$changes = git -c core.quotepath=false status --porcelain src/ README.md CHANGELOG.md LICENSE requirements.txt 2>&1
if (-not $changes) {
    Say "  没有需要提交的改动" "DarkGray"
    return
}

$changes | ForEach-Object { Say ("  " + $_) "DarkGray" }

if (-not $Message) {
    $Message = "chore: 同步源码 " + (Get-Date -Format "yyyy-MM-dd HH:mm")
}

if ($DryRun) { Say ("  [试运行] 将提交: " + $Message) "Yellow"; return }

git add -A src/
if (Test-Path "README.md")    { git add README.md }
if (Test-Path "CHANGELOG.md") { git add CHANGELOG.md }
git -c core.quotepath=false commit -m $Message 2>&1 | ForEach-Object { Say ("  " + $_) "DarkGray" }

# ---------- 4. 推送 ----------
Say ""; Say "== 4/4  推送 ==" "Cyan"
$usedProxy = $false
if ($Proxy) {
    git config http.proxy  $ProxyUrl
    git config https.proxy $ProxyUrl
    $usedProxy = $true
    Say ("  已配置代理: " + $ProxyUrl) "DarkGray"
}

try {
    $push = git push 2>&1
    $push | ForEach-Object { Say ("  " + ($_ -replace "ghp_[^@]+", "***")) "DarkGray" }
    if ($LASTEXITCODE -eq 0) {
        Say ""; Say "  推送成功" "Green"
        $sha = (git rev-parse --short HEAD)
        Say ("  提交: " + $sha) "DarkGray"
        Say ("  查看: https://github.com/huashi-qi/pdf-translator-app/commit/" + $sha) "DarkGray"
    } else {
        Say ""; Say "  推送失败。本机常遇到 github.com 直连不通，试试：" "Yellow"
        Say "    .\sync-and-push.ps1 -Proxy" "Yellow"
    }
} catch {
    Say ""; Say ("  推送异常: " + $_.Exception.Message) "Red"
    Say "  如果提示连接超时，加 -Proxy 参数重试" "Yellow"
} finally {
    if ($usedProxy) {
        git config --unset http.proxy  2>$null
        git config --unset https.proxy 2>$null
        Say "  （代理配置已清理）" "DarkGray"
    }
}

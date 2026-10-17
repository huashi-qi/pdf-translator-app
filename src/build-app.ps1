<#
    build-app.ps1 —— 生成「PDF 翻译助手」发布包

    产出：release\PDF翻译助手.zip
    用户拿到后：解压 → 双击「启动.bat」→ 拖入 PDF 即可翻译。

    前提：
      · 本机有可用的 pdf2zh_next 环境（zotero-pdf2zh-next-venv）
      · 有 uv 管理的 CPython 3.12（或用 -BasePython 指定）
      · babeldoc 离线资源已缓存（~/.cache/babeldoc，首次翻译会自动下载）
#>
[CmdletBinding()]
param(
    [string]$ServerDir  = '',
    [string]$BasePython = '',
    [string]$AppDir     = '',
    [string]$OutDir     = ''
)

$ErrorActionPreference = 'Stop'

if (-not $AppDir)     { $AppDir     = $PSScriptRoot }
if (-not $ServerDir)  { $ServerDir  = Join-Path (Split-Path -Parent $AppDir) 'server' }
if (-not $OutDir)     { $OutDir     = Join-Path (Split-Path -Parent $AppDir) 'release' }

if (-not $BasePython) {
    $cfg = Join-Path $ServerDir 'zotero-pdf2zh-next-venv\pyvenv.cfg'
    if (Test-Path -LiteralPath $cfg) {
        $m = Select-String -LiteralPath $cfg -Pattern '^home\s*=\s*(.+)$'
        if ($m) { $BasePython = $m.Matches[0].Groups[1].Value.Trim() }
    }
}
if (-not $BasePython -or -not (Test-Path -LiteralPath $BasePython)) {
    throw "找不到基础 Python，请用 -BasePython 指定（当前: '$BasePython'）"
}

function Say  { param([string]$T, [string]$C = 'Gray') Write-Host $T -ForegroundColor $C }
function Step { param([string]$T) Say ''; Say ("== " + $T + " ==") 'Cyan' }

# ---------- 安全护栏：防止 robocopy 把目录复制进自身（曾导致磁盘爆满）----------
function Assert-SafeCopy {
    param([string]$Src, [string]$Dst)
    $s = [System.IO.Path]::GetFullPath($Src).TrimEnd('\')
    $d = [System.IO.Path]::GetFullPath($Dst).TrimEnd('\')
    if ($s -eq $d) { throw "源与目标相同: $s" }
    if ($d.StartsWith($s + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "目标位于源内部，会导致无限递归复制！src=$s dst=$d"
    }
}

$Work  = Join-Path $AppDir '_build'
$Stage = Join-Path $Work 'stage'
$spSrc = Join-Path $ServerDir 'zotero-pdf2zh-next-venv\Lib\site-packages'
$cacheSrc = Join-Path $env:USERPROFILE '.cache\babeldoc'

Say ''
Say '============================================================' 'Cyan'
Say '  构建 PDF 翻译助手' 'Cyan'
Say '============================================================' 'Cyan'
Say ("  server   : " + $ServerDir)
Say ("  base py  : " + $BasePython)
Say ("  输出     : " + $OutDir)
Say ''

$null = Assert-SafeCopy $BasePython $Stage
$null = Assert-SafeCopy $spSrc $Stage
$null = Assert-SafeCopy $cacheSrc $Stage

if (Test-Path -LiteralPath $Stage) { Remove-Item -LiteralPath $Stage -Recurse -Force -ErrorAction SilentlyContinue }
$null = New-Item -ItemType Directory -Force -Path (Join-Path $Stage 'runtime')

try {
    Step '1/6  复制解释器'
    robocopy $BasePython (Join-Path $Stage 'runtime') /E /MT:16 /NFL /NDL /NJH /NJS /NP /XD __pycache__ 'Lib\site-packages' | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "复制解释器失败 ($LASTEXITCODE)" }
    Say '  OK' 'Green'

    Step '2/6  复制依赖库（剔除 gradio / 测试 / 缓存）'
    $spDst = Join-Path $Stage 'runtime\Lib\site-packages'
    $null = New-Item -ItemType Directory -Force -Path $spDst
    robocopy $spSrc $spDst /E /MT:32 /NFL /NDL /NJH /NJS /NP /XF *.pyc /XD __pycache__ tests test gradio gradio_client | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "复制依赖库失败 ($LASTEXITCODE)" }
    Say '  OK（注意：cv2/skimage/onnxruntime 是 babeldoc 硬依赖，不能删）' 'Green'

    Step '3/6  CLI 启动器'
    foreach ($e in @('pdf2zh.exe', 'pdf2zh2.exe', 'pdf2zh_next.exe')) {
        $f = Join-Path $ServerDir ('zotero-pdf2zh-next-venv\Scripts\' + $e)
        if (Test-Path -LiteralPath $f) { Copy-Item -LiteralPath $f -Destination (Join-Path $Stage ('runtime\' + $e)) -Force; Say ('  + ' + $e) }
    }
    $ruff = Join-Path $Stage 'runtime\ruff.exe'
    if (Test-Path -LiteralPath $ruff) { Remove-Item -LiteralPath $ruff -Force; Say '  - ruff.exe（开发工具）' }

    # 体积精简：只删「运行时确实用不到」的文件，均已实测验证
    # （cv2 的 ffmpeg 只用于视频读写；其余是开发/下载/包管理组件）
    $slim = @(
        'runtime\Lib\site-packages\cv2\opencv_videoio_ffmpeg500_64.dll',
        'runtime\Lib\site-packages\pymupdf\mupdf-devel',
        'runtime\Lib\site-packages\hf_xet',
        'runtime\Lib\site-packages\language_data',
        'runtime\Lib\site-packages\pip',
        'runtime\Lib\site-packages\pymupdf\__pycache__',
        'runtime\Lib\site-packages\scipy\io\__pycache__'
    )
    $saved = 0
    foreach ($rel in $slim) {
        $p = Join-Path $Stage $rel
        if (Test-Path -LiteralPath $p) {
            if ((Get-Item -LiteralPath $p).PSIsContainer) {
                $sz = (Get-ChildItem -LiteralPath $p -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
            } else {
                $sz = (Get-Item -LiteralPath $p).Length
            }
            Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue
            $saved += $sz
            Say ('  - ' + $rel + ' (' + [Math]::Round($sz / 1MB, 1) + ' MB)')
        }
    }
    Say ('  精简共省 ' + [Math]::Round($saved / 1MB, 1) + ' MB') 'Green'

    # python-docx 已不再打包 —— PDF 转 Word 功能按用户要求移除，
    # 需要 Word 版时直接用「用 Microsoft Office 打开」，由 Word 自己转换。

    Step '4/6  离线资源（字体/排版模型）'
    if (-not (Test-Path -LiteralPath $cacheSrc)) {
        throw "找不到 babeldoc 缓存 $cacheSrc。请先跑一次翻译让它自动下载，再打包。"
    }
    # babeldoc 用 Path.home()/.cache/babeldoc，程序会把 USERPROFILE 指到 cache 目录
    $cacheDst = Join-Path $Stage 'cache\.cache\babeldoc'
    $null = New-Item -ItemType Directory -Force -Path $cacheDst
    robocopy $cacheSrc $cacheDst /E /MT:16 /NFL /NDL /NJH /NJS /NP /XD __pycache__ | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "复制缓存失败 ($LASTEXITCODE)" }
    Say ('  OK (' + [Math]::Round((Get-ChildItem $cacheDst -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1) + ' MB)') 'Green'

    Step '5/6  程序与启动器'
    foreach ($f in @('app.py', 'worker.py', 'glossary_hook.py', 'glossary.csv',
                     'learn_check.py', 'pdf_editor.py', 'qa.py',
                     'pdf_viewer.py',
                     'app_sidebar.py', 'ui_sidebar.py', 'ui_widgets.py',
                     'pdf_studio.py', 'ui_studio.py',
                     '启动.vbs', '启动.bat', 'Start.bat', '启动-经典版.vbs',
                     'config.json', '使用说明.md')) {
        $s = Join-Path $AppDir $f
        if (Test-Path -LiteralPath $s) { Copy-Item -LiteralPath $s -Destination (Join-Path $Stage $f) -Force; Say ('  + ' + $f) }
        else { Say ('  !! 缺少 ' + $f) 'Yellow' }
    }
    $null = New-Item -ItemType Directory -Force -Path (Join-Path $Stage 'output')

    # 术语注入：sitecustomize 必须放在 site-packages，才会被每个解释器（含子进程）自动加载
    $scSrc = Join-Path $AppDir 'sitecustomize.py'
    if (-not (Test-Path -LiteralPath $scSrc)) { throw "缺少 sitecustomize.py（术语注入必需）" }
    Copy-Item -LiteralPath $scSrc -Destination (Join-Path $Stage 'runtime\Lib\site-packages\sitecustomize.py') -Force
    Say '  + runtime\Lib\site-packages\sitecustomize.py'

    # 打包前自检
    $must = @('app.py', 'worker.py', 'glossary_hook.py', 'glossary.csv',
              'learn_check.py', 'pdf_editor.py', 'qa.py',
              'pdf_viewer.py', 'app_sidebar.py', 'ui_sidebar.py', 'ui_widgets.py',
              'pdf_studio.py', 'ui_studio.py',
              '启动.vbs', '启动-经典版.vbs', '启动.bat', 'config.json',
              'runtime\python.exe', 'runtime\pythonw.exe',
              'runtime\pdf2zh_next.exe',
              'cache\.cache\babeldoc\fonts', 'cache\.cache\babeldoc\models')
    $bad = @($must | Where-Object { -not (Test-Path -LiteralPath (Join-Path $Stage $_)) })
    if ($bad.Count -gt 0) { throw ('打包内容不完整，缺少: ' + ($bad -join ', ')) }
    Say '  自检通过' 'Green'

    Step '6/6  压缩'
    $null = New-Item -ItemType Directory -Force -Path $OutDir
    $zip = Join-Path $OutDir 'PDF翻译助手.zip'
    if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
    Compress-Archive -Path (Join-Path $Stage '*') -DestinationPath $zip -CompressionLevel Optimal -Force
    $h = (Get-FileHash $zip -Algorithm SHA256).Hash
    $h | Set-Content -LiteralPath (Join-Path $OutDir 'PDF翻译助手.zip.sha256') -Encoding ASCII

    Say ''
    Say '============================================================' 'Green'
    Say ('  完成: ' + $zip) 'Green'
    Say ('  大小: ' + [Math]::Round((Get-Item $zip).Length / 1MB, 1) + ' MB') 'Green'
    Say ('  SHA256: ' + $h) 'Green'
    Say '============================================================' 'Green'
    Say ''
}
finally {
    if (Test-Path -LiteralPath $Stage) { Remove-Item -LiteralPath $Stage -Recurse -Force -ErrorAction SilentlyContinue }
}

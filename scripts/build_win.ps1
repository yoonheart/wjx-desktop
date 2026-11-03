# 一键打包：清干净 → 构建 → 生成 release 目录与 zip
# 用法：pwsh -File scripts\build_win.ps1
#       pwsh -File scripts\build_win.ps1 -SkipTests

param(
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Join-Path $root '.venv\Scripts\python.exe'
$pyi = Join-Path $root '.venv\Scripts\pyinstaller.exe'
if (-not (Test-Path $py)) { throw "找不到虚拟环境：$py" }

# 0) 先确认没有残留进程占着文件
Get-Process | Where-Object { $_.ProcessName -like '*问卷*' } | ForEach-Object {
    Write-Host "结束残留进程 $($_.Id)"
    Stop-Process -Id $_.Id -Force
}

# 1) 跑测试（可选）
if (-not $SkipTests) {
    Write-Host '== 跑测试 =='
    & $py -m pytest tests -q -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { throw '测试未通过，已中止打包' }
}

# 2) 清掉上一次的产物，避免新旧文件混在一起
Write-Host '== 清理旧产物 =='
foreach ($path in @('dist', 'build\build_app', 'release\问卷星助手')) {
    if (Test-Path $path) { Remove-Item $path -Recurse -Force }
}

# 3) 构建（输出到 dist，这是 PyInstaller 的默认目录）
Write-Host '== 构建 exe =='
& $pyi build_app.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw '构建失败' }

# 4) 整理成可发布目录（只会留下 exe + _internal + 说明）
Write-Host '== 整理 release =='
New-Item -ItemType Directory -Path 'release' -Force | Out-Null
Copy-Item 'dist\问卷星助手' 'release\' -Recurse -Force

# 5) 打 zip
$version = (& $py -c "from wjxdesk.version import VERSION; print(VERSION)").Trim()
$zip = "release\问卷星助手_v$version`_win64.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path 'release\问卷星助手', 'release\使用说明.txt' -DestinationPath $zip -CompressionLevel Optimal

$size = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host ''
Write-Host "完成：$zip（$size MB）"
Write-Host 'dist\ 是构建中间目录，可以随时删；要发人只用 release\ 里的 zip。'

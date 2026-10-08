# ============================================================
# 问卷星助手 —— 一键打包脚本（自包含桌面软件）
# 产物：内嵌 JDK + JCEF + Python + Edge 驱动的绿色目录，
#       新机（只有 Edge）解压即用，双击 exe 打开桌面窗口。
#
# 用法：在项目根目录运行  powershell -ExecutionPolicy Bypass -File scripts/build.ps1
# ============================================================
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$AppName = "问卷星助手"
$Version = "1.0.0"
$JdkDir = "D:\JAVA\jdk-17.0.2"          # 本机 JDK 17（用于 jpackage）
$Mvn = "D:\maven\apache-maven-3.9.11\bin\mvn.cmd"
$PythonVer = "3.11.5"                    # 内嵌 Python 版本（与 D:\python 一致）
$OutDir = Join-Path $Root "release\v4"

Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  问卷星助手 自包含打包" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan

# ---------- 步骤 1：编译 Spring Boot fat jar（含 JCEF） ----------
Write-Host "`n[1/6] 编译 Spring Boot fat jar..." -ForegroundColor Yellow
Push-Location $Root
& $Mvn -q -o -DskipTests package
if ($LASTEXITCODE -ne 0) { throw "Maven 打包失败" }
$Jar = Join-Path $Root "target\wjx-springboot-0.0.1-SNAPSHOT.jar"
if (-not (Test-Path $Jar)) { throw "找不到 jar 产物" }
Write-Host "  jar 已生成: $Jar" -ForegroundColor Green
Pop-Location

# ---------- 步骤 2：准备内嵌 Python 运行时 ----------
Write-Host "`n[2/6] 准备内嵌 Python 运行时..." -ForegroundColor Yellow
$PyDir = Join-Path $env:TEMP "wjx-python-runtime"
if (Test-Path $PyDir) { Remove-Item $PyDir -Recurse -Force }
New-Item -ItemType Directory -Path $PyDir -Force | Out-Null

# 下载 Python embeddable 版（国内镜像优先）
$PyZip = Join-Path $env:TEMP "python-$PythonVer-embed-amd64.zip"
$PyUrl = "https://mirrors.huaweicloud.com/python/$PythonVer/python-$PythonVer-embed-amd64.zip"
Write-Host "  下载 Python embeddable: $PyUrl"
Invoke-WebRequest -Uri $PyUrl -OutFile $PyZip -UseBasicParsing
Expand-Archive -Path $PyZip -DestinationPath $PyDir -Force
Remove-Item $PyZip -Force

# 修改 python311._pth，启用 site-packages（让 pip 安装的包能被找到）
$PthFile = Join-Path $PyDir "python311._pth"
if (Test-Path $PthFile) {
    $content = Get-Content $PthFile
    # 取消注释 "import site"
    $content = $content -replace '^#import site', 'import site'
    # 追加 site-packages 路径
    Add-Content $PthFile "Lib\site-packages"
    Write-Host "  已启用 site-packages"
}

# ---------- 步骤 3：安装 Python 依赖到内嵌运行时 ----------
Write-Host "`n[3/6] 安装 Python 依赖（selenium/bs4/lxml/requests）..." -ForegroundColor Yellow
$PyExe = Join-Path $PyDir "python.exe"

# embeddable 版不含 ensurepip，用 get-pip.py 引导安装 pip（国内镜像）
$GetPip = Join-Path $env:TEMP "get-pip.py"
if (-not (Test-Path $GetPip)) {
    Write-Host "  下载 get-pip.py（阿里云镜像）"
    Invoke-WebRequest -Uri "https://mirrors.aliyun.com/pypi/get-pip.py" -OutFile $GetPip -UseBasicParsing
}
& $PyExe $GetPip -i https://pypi.tuna.tsinghua.edu.cn/simple --quiet
if ($LASTEXITCODE -ne 0) { throw "pip 引导安装失败" }

& $PyExe -m pip install selenium beautifulsoup4 lxml requests `
    -i https://pypi.tuna.tsinghua.edu.cn/simple --quiet
if ($LASTEXITCODE -ne 0) { throw "Python 依赖安装失败" }
Write-Host "  Python 依赖安装完成" -ForegroundColor Green

# ---------- 步骤 4：jpackage 打包（内嵌 JDK + JCEF） ----------
Write-Host "`n[4/6] jpackage 打包桌面应用..." -ForegroundColor Yellow
$Jpackage = Join-Path $JdkDir "bin\jpackage.exe"
$InstallDir = Join-Path $OutDir $AppName

# 干净的 staging 目录：只放 fat jar，避免把 target 下的构建垃圾打进应用
$Staging = Join-Path $Root "target\staging"
if (Test-Path $Staging) { Remove-Item $Staging -Recurse -Force }
New-Item -ItemType Directory -Path $Staging -Force | Out-Null
Copy-Item $Jar $Staging -Force

# 清理旧产物
if (Test-Path $InstallDir) { Remove-Item $InstallDir -Recurse -Force }

# 用 PropertiesLauncher 作为主类，通过 -Dloader.main 指定桌面启动器
# --icon：exe 文件图标（用项目的问卷星图标，而非 jpackage 默认的 Java 图标）
$Icon = Join-Path $Root "src\main\resources\icons\app.ico"
& $Jpackage `
    --type app-image `
    --name $AppName `
    --app-version $Version `
    --input $Staging `
    --main-jar "wjx-springboot-0.0.1-SNAPSHOT.jar" `
    --main-class "org.springframework.boot.loader.launch.PropertiesLauncher" `
    --icon $Icon `
    --java-options "-Dloader.main=top.yoonheart.desktop.DesktopLauncher" `
    --java-options "-Dfile.encoding=UTF-8" `
    --java-options "-Dstdout.encoding=UTF-8" `
    --java-options "-Dstderr.encoding=UTF-8" `
    --java-options "-Dwjx.app.dir=`$APPDIR" `
    --dest $OutDir

if ($LASTEXITCODE -ne 0) { throw "jpackage 打包失败" }
Write-Host "  应用镜像已生成: $InstallDir" -ForegroundColor Green

# ---------- 步骤 5：把 Python 运行时 + 脚本复制进应用目录 ----------
Write-Host "`n[5/6] 复制 Python 运行时与脚本..." -ForegroundColor Yellow
$AppRoot = Join-Path $InstallDir "app"
# 复制 Python 运行时（含 selenium 依赖）
Copy-Item $PyDir (Join-Path $AppRoot "python") -Recurse -Force
# 复制脚本（保持与 jar 内脚本一致，便于后续调试/更新）
$ScriptsDir = Join-Path $AppRoot "scripts"
New-Item -ItemType Directory -Path $ScriptsDir -Force | Out-Null
Copy-Item (Join-Path $Root "src\main\resources\scripts\scan.py") $ScriptsDir -Force
Copy-Item (Join-Path $Root "src\main\resources\scripts\wjx2.py") $ScriptsDir -Force

# 创建 driver 目录（首次运行会自动下载与 Edge 版本匹配的驱动）
New-Item -ItemType Directory -Path (Join-Path $AppRoot "driver") -Force | Out-Null

Write-Host "  运行时与脚本已就位" -ForegroundColor Green

# ---------- 步骤 6：完成 ----------
Write-Host "`n[6/6] 打包完成！" -ForegroundColor Green
Write-Host "  应用目录: $InstallDir" -ForegroundColor Cyan
Write-Host "  可执行文件: $(Join-Path $InstallDir "$AppName.exe")" -ForegroundColor Cyan
Write-Host "`n  说明：" -ForegroundColor Cyan
Write-Host "  - 新机（只有 Edge）解压整个 app-image 目录即可运行" -ForegroundColor Cyan
Write-Host "  - 首次启动会自动下载与 Edge 版本匹配的驱动到软件目录" -ForegroundColor Cyan
Write-Host "  - 代理 IP 链接等设置在软件目录内持久化" -ForegroundColor Cyan

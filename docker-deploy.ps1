# NTRIP Caster Docker 部署脚本 (PowerShell版本)
# 用于在 Windows 环境下管理 NTRIP Caster 的 Docker 容器

param(
    [Parameter(Position=0)]
    [string]$Command = "help",
    
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$Args
)

# 设置错误处理
$ErrorActionPreference = "Stop"

# 项目配置
$PROJECT_NAME = "ntrip-caster"
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$ENV_FILE = Join-Path $SCRIPT_DIR ".env"
$ENV_EXAMPLE = Join-Path $SCRIPT_DIR ".env.example"

# 颜色定义
$Colors = @{
    Red = "Red"
    Green = "Green"
    Yellow = "Yellow"
    Blue = "Blue"
    Magenta = "Magenta"
    Cyan = "Cyan"
    White = "White"
}

# 日志函数
function Write-Log {
    param(
        [string]$Message,
        [string]$Level = "Info"
    )
    
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    
    switch ($Level) {
        "Info" { Write-Host "[$timestamp] [INFO] $Message" -ForegroundColor $Colors.Blue }
        "Success" { Write-Host "[$timestamp] [SUCCESS] $Message" -ForegroundColor $Colors.Green }
        "Warning" { Write-Host "[$timestamp] [WARNING] $Message" -ForegroundColor $Colors.Yellow }
        "Error" { Write-Host "[$timestamp] [ERROR] $Message" -ForegroundColor $Colors.Red }
        "Step" { Write-Host "[$timestamp] [STEP] $Message" -ForegroundColor $Colors.Magenta }
    }
}

# 显示横幅
function Show-Banner {
    Write-Host "" -ForegroundColor $Colors.Cyan
    Write-Host "╔══════════════════════════════════════════════════════════════╗" -ForegroundColor $Colors.Cyan
    Write-Host "║                    NTRIP Caster 部署脚本                    ║" -ForegroundColor $Colors.Cyan
    Write-Host "║                     PowerShell 版本                         ║" -ForegroundColor $Colors.Cyan
    Write-Host "╚══════════════════════════════════════════════════════════════╝" -ForegroundColor $Colors.Cyan
    Write-Host "" -ForegroundColor $Colors.Cyan
}

# 检查 Docker 环境
function Test-DockerEnvironment {
    Write-Log "检查 Docker 环境..." "Step"
    
    # 检查 Docker
    try {
        $dockerVersion = docker --version
        Write-Log "Docker 版本: $dockerVersion" "Info"
    }
    catch {
        Write-Log "Docker 未安装或未启动" "Error"
        Write-Log "请安装 Docker Desktop: https://www.docker.com/products/docker-desktop" "Info"
        exit 1
    }
    
    # 检查 Docker Compose
    try {
        $composeVersion = docker compose version
        Write-Log "Docker Compose 版本: $composeVersion" "Info"
        $script:DOCKER_COMPOSE_CMD = "docker compose"
    }
    catch {
        try {
            $composeVersion = docker-compose --version
            Write-Log "Docker Compose 版本: $composeVersion" "Info"
            $script:DOCKER_COMPOSE_CMD = "docker-compose"
        }
        catch {
            Write-Log "Docker Compose 未安装" "Error"
            exit 1
        }
    }
    
    # 检查 Docker 守护进程
    try {
        docker info | Out-Null
        Write-Log "Docker 守护进程运行正常" "Success"
    }
    catch {
        Write-Log "Docker 守护进程未运行，请启动 Docker Desktop" "Error"
        exit 1
    }
}

# 加载环境变量
function Import-EnvironmentVariables {
    if (Test-Path $ENV_FILE) {
        Get-Content $ENV_FILE | ForEach-Object {
            if ($_ -match '^([^#][^=]+)=(.*)$') {
                [Environment]::SetEnvironmentVariable($matches[1], $matches[2], "Process")
            }
        }
        Write-Log "已加载环境变量" "Info"
    } else {
        Write-Log ".env 文件不存在，使用默认配置" "Warning"
    }
}

# 构建 Docker Compose 命令
function Build-ComposeCommand {
    param([string[]]$ComposeArgs)
    
    $environment = $env:ENVIRONMENT
    if (-not $environment) { $environment = "development" }
    
    $profiles = $env:PROFILES
    if (-not $profiles) { $profiles = "dev" }
    
    $composeFiles = @("-f", "docker-compose.yml")
    
    if ($environment -eq "production") {
        $composeFiles += @("-f", "docker-compose.prod.yml")
    } else {
        $composeFiles += @("-f", "docker-compose.override.yml")
    }
    
    $profileArgs = @()
    if ($profiles) {
        $profileList = $profiles -split ","
        foreach ($profile in $profileList) {
            $profileArgs += @("--profile", $profile.Trim())
        }
    }
    
    $fullCommand = @($script:DOCKER_COMPOSE_CMD) + $composeFiles + $profileArgs + $ComposeArgs
    return $fullCommand -join " "
}

# 执行 Docker Compose 命令
function Invoke-ComposeCommand {
    param([string[]]$ComposeArgs)
    
    $command = Build-ComposeCommand $ComposeArgs
    Write-Log "执行命令: $command" "Info"
    
    try {
        Invoke-Expression $command
        return $LASTEXITCODE
    }
    catch {
        Write-Log "命令执行失败: $_" "Error"
        return 1
    }
}

# 创建必要目录
function New-RequiredDirectories {
    Write-Log "创建必要目录..." "Step"
    
    $directories = @(
        "data",
        "logs",
        "secrets",
        "nginx/logs",
        "redis",
        "monitoring/prometheus/rules",
        "monitoring/grafana/provisioning/datasources",
        "monitoring/grafana/provisioning/dashboards",
        "monitoring/grafana/dashboards",
        "backup"
    )
    
    foreach ($dir in $directories) {
        $fullPath = Join-Path $SCRIPT_DIR $dir
        if (-not (Test-Path $fullPath)) {
            New-Item -ItemType Directory -Path $fullPath -Force | Out-Null
            Write-Log "创建目录: $dir" "Info"
        }
    }
    
    # 设置权限（Windows 下的等效操作）
    try {
        $dataPath = Join-Path $SCRIPT_DIR "data"
        $logsPath = Join-Path $SCRIPT_DIR "logs"
        
        # 确保当前用户有完全控制权限
        icacls $dataPath /grant "${env:USERNAME}:(OI)(CI)F" /T | Out-Null
        icacls $logsPath /grant "${env:USERNAME}:(OI)(CI)F" /T | Out-Null
        
        Write-Log "目录权限设置完成" "Success"
    }
    catch {
        Write-Log "权限设置失败，但不影响使用" "Warning"
    }
}

# 创建环境文件
function New-EnvironmentFile {
    Write-Log "创建环境配置文件..." "Step"
    
    if (-not (Test-Path $ENV_FILE)) {
        if (Test-Path $ENV_EXAMPLE) {
            Copy-Item $ENV_EXAMPLE $ENV_FILE
            Write-Log "已创建 .env 文件" "Success"
        } else {
            Write-Log ".env.example 文件不存在" "Error"
            return
        }
    }
    
    # 更新环境变量
    $content = Get-Content $ENV_FILE
    $environment = $env:ENVIRONMENT
    if (-not $environment) { $environment = "development" }
    
    $content = $content -replace '^ENVIRONMENT=.*', "ENVIRONMENT=$environment"
    $content = $content -replace '^PROJECT_NAME=.*', "PROJECT_NAME=$PROJECT_NAME"
    $content = $content -replace '^TZ=.*', "TZ=Asia/Shanghai"
    
    Set-Content -Path $ENV_FILE -Value $content
    Write-Log "环境配置文件更新完成" "Success"
}

# 健康检查
function Test-ServiceHealth {
    Write-Log "执行健康检查..." "Step"
    
    try {
        if (Test-Path "healthcheck.py") {
            python healthcheck.py
        } else {
            Write-Log "健康检查脚本不存在，跳过检查" "Warning"
        }
    }
    catch {
        Write-Log "健康检查失败: $_" "Error"
    }
}

# 显示服务信息
function Show-ServiceInfo {
    Write-Log "服务信息:" "Step"
    
    # 获取本机IP
    $localIP = (Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias "以太网*" | Select-Object -First 1).IPAddress
    if (-not $localIP) {
        $localIP = "localhost"
    }
    
    Write-Host ""
    Write-Host "📡 NTRIP Caster 服务:" -ForegroundColor $Colors.Cyan
    Write-Host "   - NTRIP 端口: ntrip://${localIP}:2101" -ForegroundColor $Colors.White
    Write-Host "   - Web 管理界面: http://${localIP}:5757" -ForegroundColor $Colors.White
    
    $profiles = $env:PROFILES
    if ($profiles -and ($profiles -match "monitoring" -or $profiles -match "full")) {
        Write-Host ""
        Write-Host "📊 监控服务:" -ForegroundColor $Colors.Cyan
        Write-Host "   - Prometheus: http://${localIP}:9090" -ForegroundColor $Colors.White
        Write-Host "   - Grafana: http://${localIP}:3000 (admin/admin123)" -ForegroundColor $Colors.White
    }
    
    $environment = $env:ENVIRONMENT
    if ($environment -eq "development") {
        Write-Host ""
        Write-Host "🛠️ 开发工具:" -ForegroundColor $Colors.Cyan
        Write-Host "   - Adminer (数据库管理): http://${localIP}:8081" -ForegroundColor $Colors.White
        Write-Host "   - Dozzle (日志查看): http://${localIP}:8082" -ForegroundColor $Colors.White
        Write-Host "   - cAdvisor (容器监控): http://${localIP}:8083" -ForegroundColor $Colors.White
    }
    
    Write-Host ""
}

# 显示帮助信息
function Show-Help {
    Write-Host ""
    Write-Host "NTRIP Caster Docker 部署脚本 (PowerShell版本)" -ForegroundColor $Colors.Cyan
    Write-Host ""
    Write-Host "用法: .\docker-deploy.ps1 <命令> [选项]" -ForegroundColor $Colors.White
    Write-Host ""
    Write-Host "基本命令:" -ForegroundColor $Colors.Yellow
    Write-Host "  up              启动服务" -ForegroundColor $Colors.White
    Write-Host "  down            停止服务" -ForegroundColor $Colors.White
    Write-Host "  restart         重启服务" -ForegroundColor $Colors.White
    Write-Host "  status          查看服务状态" -ForegroundColor $Colors.White
    Write-Host "  logs            查看服务日志" -ForegroundColor $Colors.White
    Write-Host "  build           构建镜像" -ForegroundColor $Colors.White
    Write-Host "  pull            拉取镜像" -ForegroundColor $Colors.White
    Write-Host "  clean           清理资源" -ForegroundColor $Colors.White
    Write-Host ""
    Write-Host "管理命令:" -ForegroundColor $Colors.Yellow
    Write-Host "  health          健康检查" -ForegroundColor $Colors.White
    Write-Host "  info            显示服务信息" -ForegroundColor $Colors.White
    Write-Host "  backup          备份数据" -ForegroundColor $Colors.White
    Write-Host "  restore         恢复数据" -ForegroundColor $Colors.White
    Write-Host "  update          更新服务" -ForegroundColor $Colors.White
    Write-Host ""
    Write-Host "环境变量:" -ForegroundColor $Colors.Yellow
    Write-Host "  ENVIRONMENT     部署环境 (development|production)" -ForegroundColor $Colors.White
    Write-Host "  PROFILES        服务配置文件 (dev|prod|monitoring|full)" -ForegroundColor $Colors.White
    Write-Host ""
    Write-Host "示例:" -ForegroundColor $Colors.Yellow
    Write-Host "  .\docker-deploy.ps1 up -d" -ForegroundColor $Colors.White
    Write-Host "  `$env:ENVIRONMENT='production'; .\docker-deploy.ps1 up" -ForegroundColor $Colors.White
    Write-Host "  `$env:PROFILES='monitoring'; .\docker-deploy.ps1 restart" -ForegroundColor $Colors.White
    Write-Host ""
}

# 主函数
function Main {
    param([string]$Command, [string[]]$Args)
    
    Show-Banner
    
    # 检查是否在正确目录
    if (-not (Test-Path "docker-compose.yml")) {
        Write-Log "请在 NTRIP Caster 项目根目录下运行此脚本" "Error"
        exit 1
    }
    
    # 检查 Docker 环境
    Test-DockerEnvironment
    
    # 加载环境变量
    Import-EnvironmentVariables
    
    switch ($Command.ToLower()) {
        "help" {
            Show-Help
        }
        "check" {
            Write-Log "Docker 环境检查完成" "Success"
        }
        "create_directories" {
            New-RequiredDirectories
        }
        "create_env" {
            New-EnvironmentFile
        }
        "up" {
            Write-Log "启动服务..." "Step"
            $exitCode = Invoke-ComposeCommand (@("up") + $Args)
            if ($exitCode -eq 0) {
                Start-Sleep -Seconds 5
                Test-ServiceHealth
                Show-ServiceInfo
            }
        }
        "down" {
            Write-Log "停止服务..." "Step"
            Invoke-ComposeCommand (@("down") + $Args)
        }
        "restart" {
            Write-Log "重启服务..." "Step"
            Invoke-ComposeCommand (@("restart") + $Args)
            Start-Sleep -Seconds 5
            Test-ServiceHealth
        }
        "status" {
            Invoke-ComposeCommand @("ps")
        }
        "logs" {
            Invoke-ComposeCommand (@("logs") + $Args)
        }
        "build" {
            Write-Log "构建镜像..." "Step"
            Invoke-ComposeCommand (@("build") + $Args)
        }
        "pull" {
            Write-Log "拉取镜像..." "Step"
            Invoke-ComposeCommand (@("pull") + $Args)
        }
        "clean" {
            Write-Log "清理资源..." "Step"
            Invoke-ComposeCommand @("down", "--volumes", "--remove-orphans")
            docker system prune -f
        }
        "health" {
            Test-ServiceHealth
        }
        "info" {
            Show-ServiceInfo
        }
        "backup" {
            Write-Log "备份数据..." "Step"
            $backupDir = Join-Path $SCRIPT_DIR "backup"
            $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
            $backupFile = Join-Path $backupDir "ntrip_backup_$timestamp.zip"
            
            if (-not (Test-Path $backupDir)) {
                New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
            }
            
            $dataDir = Join-Path $SCRIPT_DIR "data"
            if (Test-Path $dataDir) {
                Compress-Archive -Path $dataDir -DestinationPath $backupFile -Force
                Write-Log "数据备份完成: $backupFile" "Success"
            } else {
                Write-Log "数据目录不存在" "Warning"
            }
        }
        "restore" {
            Write-Log "恢复数据..." "Step"
            if ($Args.Count -gt 0) {
                $backupFile = $Args[0]
                if (Test-Path $backupFile) {
                    $dataDir = Join-Path $SCRIPT_DIR "data"
                    Expand-Archive -Path $backupFile -DestinationPath $dataDir -Force
                    Write-Log "数据恢复完成" "Success"
                } else {
                    Write-Log "备份文件不存在: $backupFile" "Error"
                }
            } else {
                Write-Log "请指定备份文件路径" "Error"
            }
        }
        "update" {
            Write-Log "更新服务..." "Step"
            Invoke-ComposeCommand @("pull")
            Invoke-ComposeCommand @("up", "-d")
            Write-Log "服务更新完成" "Success"
        }
        default {
            Write-Log "未知命令: $Command" "Error"
            Show-Help
            exit 1
        }
    }
}

# 脚本入口
if ($MyInvocation.InvocationName -ne '.') {
    Main $Command $Args
}
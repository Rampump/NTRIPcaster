@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM NTRIP Caster Docker 部署脚本 (批处理版本)
REM 用于在 Windows 环境下管理 NTRIP Caster 的 Docker 容器

REM 项目配置
set "PROJECT_NAME=ntrip-caster"
set "SCRIPT_DIR=%~dp0"
set "ENV_FILE=%SCRIPT_DIR%.env"

REM 颜色定义 (Windows 10+ 支持 ANSI 颜色)
set "RED=[31m"
set "GREEN=[32m"
set "YELLOW=[33m"
set "BLUE=[34m"
set "PURPLE=[35m"
set "CYAN=[36m"
set "NC=[0m"

REM 启用 ANSI 颜色支持
reg add HKCU\Console /v VirtualTerminalLevel /t REG_DWORD /d 1 /f >nul 2>&1

REM 获取命令参数
set "COMMAND=%1"
if "%COMMAND%"=="" set "COMMAND=help"

REM 日志函数
:log_info
echo %BLUE%[INFO]%NC% %~1
goto :eof

:log_success
echo %GREEN%[SUCCESS]%NC% %~1
goto :eof

:log_warning
echo %YELLOW%[WARNING]%NC% %~1
goto :eof

:log_error
echo %RED%[ERROR]%NC% %~1
goto :eof

:log_step
echo %PURPLE%[STEP]%NC% %~1
goto :eof

REM 显示横幅
:show_banner
echo %CYAN%
echo ╔══════════════════════════════════════════════════════════════╗
echo ║                    NTRIP Caster 部署脚本                    ║
echo ║                      批处理版本                              ║
echo ╚══════════════════════════════════════════════════════════════╝
echo %NC%
goto :eof

REM 检查 Docker 环境
:check_docker
call :log_step "检查 Docker 环境..."

REM 检查 Docker
docker --version >nul 2>&1
if errorlevel 1 (
    call :log_error "Docker 未安装，请先安装 Docker Desktop"
    echo 下载地址: https://www.docker.com/products/docker-desktop
    exit /b 1
)

REM 检查 Docker Compose
docker compose version >nul 2>&1
if errorlevel 1 (
    docker-compose --version >nul 2>&1
    if errorlevel 1 (
        call :log_error "Docker Compose 未安装"
        exit /b 1
    ) else (
        set "DOCKER_COMPOSE_CMD=docker-compose"
    )
) else (
    set "DOCKER_COMPOSE_CMD=docker compose"
)

REM 检查 Docker 服务状态
docker info >nul 2>&1
if errorlevel 1 (
    call :log_error "Docker 服务未运行，请启动 Docker Desktop"
    exit /b 1
)

call :log_success "Docker 环境检查完成"
goto :eof

REM 加载环境变量
:load_env
if exist "%ENV_FILE%" (
    for /f "usebackq tokens=1,2 delims==" %%a in ("%ENV_FILE%") do (
        if not "%%a"=="" if not "%%a:~0,1"=="#" (
            set "%%a=%%b"
        )
    )
    call :log_info "已加载环境变量"
) else (
    call :log_warning ".env 文件不存在，使用默认配置"
)
goto :eof

REM 构建 Docker Compose 命令
:build_compose_cmd
if "%ENVIRONMENT%"=="" set "ENVIRONMENT=development"
if "%PROFILES%"=="" set "PROFILES=dev"

set "COMPOSE_FILES=-f docker-compose.yml"

if "%ENVIRONMENT%"=="production" (
    set "COMPOSE_FILES=%COMPOSE_FILES% -f docker-compose.prod.yml"
) else (
    set "COMPOSE_FILES=%COMPOSE_FILES% -f docker-compose.override.yml"
)

set "PROFILE_ARGS="
for %%p in (%PROFILES:,= %) do (
    set "PROFILE_ARGS=!PROFILE_ARGS! --profile %%p"
)

set "FULL_COMPOSE_CMD=%DOCKER_COMPOSE_CMD% %COMPOSE_FILES% %PROFILE_ARGS%"
goto :eof

REM 执行 Docker Compose 命令
:run_compose
call :build_compose_cmd
set "FULL_CMD=%FULL_COMPOSE_CMD% %*"
call :log_info "执行命令: %FULL_CMD%"
%FULL_CMD%
goto :eof

REM 创建必要目录
:create_directories
call :log_step "创建必要目录..."

set "DIRS=data logs secrets nginx\logs redis monitoring\prometheus\rules monitoring\grafana\provisioning\datasources monitoring\grafana\provisioning\dashboards monitoring\grafana\dashboards backup"

for %%d in (%DIRS%) do (
    if not exist "%%d" (
        mkdir "%%d" 2>nul
        call :log_info "创建目录: %%d"
    )
)

call :log_success "目录创建完成"
goto :eof

REM 健康检查
:health_check
call :log_step "执行健康检查..."

if exist "healthcheck.py" (
    python healthcheck.py
) else (
    call :log_warning "健康检查脚本不存在，跳过检查"
)
goto :eof

REM 显示服务信息
:show_info
call :log_step "服务信息:"

REM 获取本机IP
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
    for /f "tokens=1" %%b in ("%%a") do (
        set "LOCAL_IP=%%b"
        goto :ip_found
    )
)
set "LOCAL_IP=localhost"

:ip_found
echo.
echo 📡 NTRIP Caster 服务:
echo    - NTRIP 端口: ntrip://%LOCAL_IP%:2101
echo    - Web 管理界面: http://%LOCAL_IP%:5757

echo %PROFILES% | findstr /c:"monitoring" >nul
if not errorlevel 1 (
    echo.
    echo 📊 监控服务:
    echo    - Prometheus: http://%LOCAL_IP%:9090
    echo    - Grafana: http://%LOCAL_IP%:3000 ^(admin/admin123^)
)

if "%ENVIRONMENT%"=="development" (
    echo.
    echo 🛠️ 开发工具:
    echo    - Adminer ^(数据库管理^): http://%LOCAL_IP%:8081
    echo    - Dozzle ^(日志查看^): http://%LOCAL_IP%:8082
    echo    - cAdvisor ^(容器监控^): http://%LOCAL_IP%:8083
)

echo.
goto :eof

REM 显示帮助信息
:show_help
echo.
echo NTRIP Caster Docker 部署脚本 ^(批处理版本^)
echo.
echo 用法: docker-deploy.bat ^<命令^> [选项]
echo.
echo 基本命令:
echo   up              启动服务
echo   down            停止服务
echo   restart         重启服务
echo   status          查看服务状态
echo   logs            查看服务日志
echo   build           构建镜像
echo   pull            拉取镜像
echo   clean           清理资源
echo.
echo 管理命令:
echo   health          健康检查
echo   info            显示服务信息
echo   backup          备份数据
echo   create_dirs     创建必要目录
echo.
echo 环境变量:
echo   ENVIRONMENT     部署环境 ^(development^|production^)
echo   PROFILES        服务配置文件 ^(dev^|prod^|monitoring^|full^)
echo.
echo 示例:
echo   docker-deploy.bat up -d
echo   set ENVIRONMENT=production ^&^& docker-deploy.bat up
echo   set PROFILES=monitoring ^&^& docker-deploy.bat restart
echo.
goto :eof

REM 主函数
:main
call :show_banner

REM 检查是否在正确目录
if not exist "docker-compose.yml" (
    call :log_error "请在 NTRIP Caster 项目根目录下运行此脚本"
    pause
    exit /b 1
)

REM 检查 Docker 环境
call :check_docker
if errorlevel 1 exit /b 1

REM 加载环境变量
call :load_env

REM 执行命令
if "%COMMAND%"=="help" goto :show_help
if "%COMMAND%"=="check" (
    call :log_success "Docker 环境检查完成"
    goto :end
)
if "%COMMAND%"=="create_dirs" (
    call :create_directories
    goto :end
)
if "%COMMAND%"=="up" (
    call :log_step "启动服务..."
    call :run_compose up %2 %3 %4 %5 %6 %7 %8 %9
    if not errorlevel 1 (
        timeout /t 5 /nobreak >nul
        call :health_check
        call :show_info
    )
    goto :end
)
if "%COMMAND%"=="down" (
    call :log_step "停止服务..."
    call :run_compose down %2 %3 %4 %5 %6 %7 %8 %9
    goto :end
)
if "%COMMAND%"=="restart" (
    call :log_step "重启服务..."
    call :run_compose restart %2 %3 %4 %5 %6 %7 %8 %9
    timeout /t 5 /nobreak >nul
    call :health_check
    goto :end
)
if "%COMMAND%"=="status" (
    call :run_compose ps
    goto :end
)
if "%COMMAND%"=="logs" (
    call :run_compose logs %2 %3 %4 %5 %6 %7 %8 %9
    goto :end
)
if "%COMMAND%"=="build" (
    call :log_step "构建镜像..."
    call :run_compose build %2 %3 %4 %5 %6 %7 %8 %9
    goto :end
)
if "%COMMAND%"=="pull" (
    call :log_step "拉取镜像..."
    call :run_compose pull %2 %3 %4 %5 %6 %7 %8 %9
    goto :end
)
if "%COMMAND%"=="clean" (
    call :log_step "清理资源..."
    call :run_compose down --volumes --remove-orphans
    docker system prune -f
    goto :end
)
if "%COMMAND%"=="health" (
    call :health_check
    goto :end
)
if "%COMMAND%"=="info" (
    call :show_info
    goto :end
)
if "%COMMAND%"=="backup" (
    call :log_step "备份数据..."
    if not exist "backup" mkdir "backup"
    for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set "dt=%%a"
    set "timestamp=!dt:~0,8!_!dt:~8,6!"
    if exist "data" (
        powershell -Command "Compress-Archive -Path 'data' -DestinationPath 'backup\ntrip_backup_!timestamp!.zip' -Force"
        call :log_success "数据备份完成: backup\ntrip_backup_!timestamp!.zip"
    ) else (
        call :log_warning "数据目录不存在"
    )
    goto :end
)

REM 未知命令
call :log_error "未知命令: %COMMAND%"
call :show_help

:end
goto :eof

REM 脚本入口
call :main %*
# NTRIP Caster v2.2.0 Docker镜像
# 使用多阶段构建优化镜像大小
FROM python:3.11-slim AS builder

# 设置构建时环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

# 安装构建依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 创建虚拟环境
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 复制并安装Python依赖
COPY requirements.txt .
# 首先升级setuptools到安全版本以修复CVE-2025-47273和CVE-2024-6345
RUN pip install --upgrade pip setuptools>=78.1.1 wheel && \
    pip install --no-cache-dir -r requirements.txt

# 生产镜像
FROM python:3.11-slim AS production

# 设置标签信息
LABEL maintainer="2rtk <i@jia.by>" \
      version="2.2.0" \
      description="High-performance NTRIP Caster with RTCM parsing" \
      org.opencontainers.image.title="NTRIP Caster" \
      org.opencontainers.image.description="High-performance NTRIP Caster with RTCM parsing" \
      org.opencontainers.image.version="2.2.0" \
      org.opencontainers.image.vendor="2RTK" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/Rampump/NTRIPcaster"

# 设置环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PATH="/opt/venv/bin:$PATH" \
    DEBIAN_FRONTEND=noninteractive \
    TZ=UTC

# 安装运行时依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    curl \
    ca-certificates \
    tzdata \
    tini \
    gosu \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# 创建非root用户和组
RUN groupadd -r -g 1000 ntrip && \
    useradd -r -u 1000 -g ntrip -d /app -s /bin/bash ntrip

# 设置工作目录
WORKDIR /app

# 从构建阶段复制虚拟环境
COPY --from=builder /opt/venv /opt/venv

# 确保生产环境也使用安全版本的setuptools
RUN pip install --upgrade setuptools>=78.1.1

# 复制应用代码（选择性复制，避免复制不必要的文件）
COPY --chown=ntrip:ntrip main.py healthcheck.py config.ini.example ./
COPY --chown=ntrip:ntrip src/ ./src/
COPY --chown=ntrip:ntrip pyrtcm/ ./pyrtcm/
COPY --chown=ntrip:ntrip static/ ./static/
COPY --chown=ntrip:ntrip templates/ ./templates/

# 创建必要的目录并设置权限
RUN mkdir -p /app/logs /app/data /app/config && \
    chown -R ntrip:ntrip /app && \
    chmod -R 755 /app && \
    chmod +x /app/main.py /app/healthcheck.py

# 创建数据卷挂载点
VOLUME ["/app/logs", "/app/data", "/app/config"]

# 暴露端口
EXPOSE 2101 5757

# 健康检查
HEALTHCHECK --interval=30s --timeout=15s --start-period=90s --retries=3 \
    CMD python /app/healthcheck.py || exit 1

# 创建启动脚本
RUN echo '#!/bin/bash\n\
set -e\n\
\n\
# 确保目录存在并设置正确权限\n\
echo "设置目录权限..."\n\
mkdir -p /app/logs /app/data /app/config\n\
chown -R ntrip:ntrip /app/logs /app/data /app/config\n\
chmod -R 755 /app/logs /app/data /app/config\n\
\n\
# 初始化配置文件（如果不存在）\n\
if [ ! -f "/app/config/config.ini" ]; then\n\
    echo "初始化配置文件..."\n\
    cp /app/config.ini.example /app/config/config.ini\n\
    chown ntrip:ntrip /app/config/config.ini\n\
fi\n\
\n\
# 设置配置文件路径\n\
export NTRIP_CONFIG_FILE="/app/config/config.ini"\n\
\n\
# 切换到ntrip用户并启动应用\n\
echo "启动NTRIP Caster..."\n\
exec gosu ntrip python /app/main.py' > /app/entrypoint.sh && \
    chmod +x /app/entrypoint.sh && \
    chown ntrip:ntrip /app/entrypoint.sh

# 使用tini作为init进程
ENTRYPOINT ["/usr/bin/tini", "--"]

# 启动命令
CMD ["/app/entrypoint.sh"]
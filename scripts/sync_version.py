#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_version.py - 版本号同步工具（全项目唯一事实源在根目录 VERSION 文件）

用法：
    python3 scripts/sync_version.py           # 把 VERSION 同步到所有派生位置
    python3 scripts/sync_version.py --check   # 只检查不修改，不一致则退出码 1（可用于 CI/发布前校验）

维护约定：
    1. 发版时只改根目录 VERSION 文件里的那一行版本号；
    2. 执行本脚本，代码/配置/脚本/文档里的派生版本号自动对齐；
    3. 不要手工去各文件里改版本号 —— 下次同步会被覆盖回去，且容易漏。

哪些会被改（派生副本）：
    config.ini.example、.env.example、docker-compose(.prod).yml、docker-deploy.sh、
    Dockerfile、README(.md/-zh.md)、4 个安装/Docker 教程、update.sh 的 SCRIPT_VERSION。

哪些不会改（历史记录，必须保留原样）：
    教程末尾的更新日志（形如 `- **v2.3.0**: ...`）、git tag、已发布 Release 说明。
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"

# (相对路径, 正则, 替换模板, 说明)
# 替换模板里的 {v} 会被替换成版本号。正则统一用 MULTILINE。
RULES = [
    # ---------- 运行时配置 ----------
    ("config.ini.example", r"^version\s*=\s*\S+$", "version = {v}", "示例配置版本号"),
    # 本地实际配置（已 gitignore，只在本机存在时顺带对齐，避免看配置时产生困惑）
    ("config.ini", r"^version\s*=\s*\S+$", "version = {v}", "本地配置版本号"),
    (".env.example", r"^APP_VERSION=.*$", "APP_VERSION={v}", "示例环境变量版本号"),

    # ---------- Docker ----------
    ("docker-compose.yml", r"image:\s*ntripcaster:\S+", "image: ntripcaster:{v}", "compose 镜像标签"),
    ("docker-compose.prod.yml", r"image:\s*ntripcaster:\S+", "image: ntripcaster:{v}", "生产 compose 镜像标签"),
    ("Dockerfile", r"^# NTRIP Caster v\S+ Docker镜像$", "# NTRIP Caster v{v} Docker镜像", "Dockerfile 头注释"),
    ("Dockerfile", r'^(\s*)version="\d+\.\d+\.\d+"', r'\g<1>version="{v}"', "Dockerfile LABEL version"),
    ("Dockerfile", r'(org\.opencontainers\.image\.version=)"\d+\.\d+\.\d+"',
     r'\g<1>"{v}"', "OCI version 标签"),

    # ---------- 部署脚本 ----------
    ("docker-deploy.sh", r'^IMAGE_TAG="[^"]*"$', 'IMAGE_TAG="{v}"', "docker-deploy 镜像标签"),
    ("docker-deploy.sh", r"^# NTRIP Caster Docker\s*部署脚本 v\S+$",
     "# NTRIP Caster Docker部署脚本 v{v}", "docker-deploy 头注释"),
    ("docker-deploy.sh", r"(Docker\s*部署脚本 v)\d+\.\d+\.\d+",
     r"\g<1>{v}", "docker-deploy 运行时横幅"),
    ("update.sh", r'^SCRIPT_VERSION="[^"]*"$', 'SCRIPT_VERSION="{v}"', "update.sh 脚本版本"),
    ("update.sh", r"^# NTRIP Caster 一键无损更新脚本 v\S+$",
     "# NTRIP Caster 一键无损更新脚本 v{v}", "update.sh 头注释"),

    # ---------- 文档标题 / 版本信息 ----------
    ("README.md", r"^# 2RTK NTRIP Caster v\S+$", "# 2RTK NTRIP Caster v{v}", "README 标题"),
    ("README-zh.md", r"^# 2RTK NTRIP Caster v\S+$", "# 2RTK NTRIP Caster v{v}", "中文 README 标题"),
    ("DOCKER-TUTORIAL.md", r"NTRIP Caster v\d+\.\d+\.\d+", "NTRIP Caster v{v}", "Docker 教程"),
    ("DOCKER-TUTORIAL-EN.md", r"NTRIP Caster v\d+\.\d+\.\d+", "NTRIP Caster v{v}", "Docker 教程 EN"),
    ("INSTALL-TUTORIAL.md", r"NTRIP Caster v\d+\.\d+\.\d+", "NTRIP Caster v{v}", "安装教程"),
    ("INSTALL-TUTORIAL-EN.md", r"NTRIP Caster v\d+\.\d+\.\d+", "NTRIP Caster v{v}", "安装教程 EN"),
    ("INSTALL-TUTORIAL.md", r"^version\s*=\s*\d+\.\d+\.\d+$", "version = {v}", "安装教程示例配置"),
    ("INSTALL-TUTORIAL-EN.md", r"^version\s*=\s*\d+\.\d+\.\d+$", "version = {v}", "安装教程示例配置 EN"),
]


def read_version() -> str:
    """读取 VERSION 文件里的版本号（首个非空、非注释行）。"""
    if not VERSION_FILE.exists():
        sys.exit(f"[ERROR] 未找到版本文件: {VERSION_FILE}")
    for line in VERSION_FILE.read_text(encoding="utf-8").splitlines():
        v = line.strip()
        if v and not v.startswith("#"):
            if not re.fullmatch(r"\d+\.\d+\.\d+", v):
                sys.exit(f"[ERROR] VERSION 内容格式不合法（应为 x.y.z）: {v!r}")
            return v
    sys.exit("[ERROR] VERSION 文件为空")


def main() -> int:
    v = read_version()
    check_only = "--check" in sys.argv

    print(f"VERSION = {v}" + ("（仅检查）" if check_only else ""))
    print("-" * 60)

    changed_files = 0
    stale = []

    for rel, pattern, repl, label in RULES:
        path = ROOT / rel
        if not path.exists():
            print(f"[SKIP] {rel:<26} 文件不存在")
            continue

        original = path.read_text(encoding="utf-8")
        target = repl.format(v=v)
        updated, count = re.subn(pattern, target, original, flags=re.MULTILINE)

        if count == 0:
            print(f"[MISS] {rel:<26} {label}：未匹配到（正则可能需要更新）")
            continue

        if updated == original:
            print(f"[OK]   {rel:<26} {label}")
            continue

        stale.append(rel)
        if check_only:
            print(f"[DIFF] {rel:<26} {label}：与 VERSION 不一致")
        else:
            path.write_text(updated, encoding="utf-8")
            changed_files += 1
            print(f"[SYNC] {rel:<26} {label}（{count} 处）")

    print("-" * 60)
    if check_only:
        if stale:
            print(f"[FAIL] {len(stale)} 个文件版本号与 VERSION 不一致，请执行: python3 scripts/sync_version.py")
            return 1
        print("[PASS] 所有派生位置版本号一致")
        return 0

    print(f"完成：{changed_files} 个文件已更新" if changed_files else "完成：所有文件已是目标版本")
    return 0


if __name__ == "__main__":
    sys.exit(main())

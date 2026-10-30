#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
src包 - NTRIP Caster核心模块
"""

import os


def _read_version() -> str:
    """从项目根目录的 VERSION 文件读取版本号。

    版本号全项目只维护 VERSION 一个文件（改完执行 scripts/sync_version.py 同步派生副本），
    这里不写死字面量，也不 import config，避免仅取版本号就触发配置加载。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(os.path.join(root, 'VERSION'), encoding='utf-8') as f:
            for line in f:
                v = line.strip()
                if v and not v.startswith('#'):
                    return v
    except OSError:
        pass
    return os.environ.get('APP_VERSION', '').strip() or '0.0.0'


__version__ = _read_version()
__author__ = "2RTK"

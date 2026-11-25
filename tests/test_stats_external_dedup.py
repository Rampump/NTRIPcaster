#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""首页在线挂载点分组：本机同名挂载点不应被外部缓存重复列出。

回归场景：本机在线挂载点 2RTK，外部源 2rtk-prod 的 STR 缓存里也有 2RTK。
get_all_str_data_split / generate_mount_list 都已做本地优先去重，
但 get_statistics 曾直接 append fused_rows，导致首页「外部 STR 可用基准站」
里多出一条 2RTK。本测试锁住两个口径：
  - mounts 里同名挂载点只出现一次，且标为 local
  - external_available_mounts 不含同名项
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import src.connection as connection  # noqa: E402
from src.connection import ConnectionManager  # noqa: E402


class _FakeExternal:
    def __init__(self, rows):
        self.rows = rows

    def get_fused_rows(self):
        return list(self.rows)

    def get_relay_status(self):
        return {}

    def get_stats(self, mount):
        return {}


def _row(mount, source):
    return {
        'mount': mount, 'str_line': f'STR;{mount};x;RTCM 3;1;0;GPS;x;CHN;0;0;0;0;x;N;B;N;9600;x',
        'lat': 0, 'lon': 0, 'priority': 0, 'source_id': 1,
        'source_name': source,
    }


def test_statistics_skips_external_mount_with_local_name(monkeypatch):
    fake = _FakeExternal([
        _row('2RTK', '2rtk-prod'),      # 与本机同名
        _row('EXT-ONLY', 'auscors-prod'),
    ])
    monkeypatch.setattr(connection, '_external_manager', lambda: fake)

    cm = ConnectionManager()
    cm.add_mount_connection('2RTK', '1.1.1.1')

    stats = cm.get_statistics()
    entries = [m for m in stats['mounts'] if m['mount_name'] == '2RTK']

    assert len(entries) == 1, f"同名挂载点应只出现一次，实际={entries}"
    assert entries[0]['source_type'] == 'local', f"应保留本地行，实际={entries[0]}"
    assert any(m['mount_name'] == 'EXT-ONLY' and m['source_type'] == 'external'
               for m in stats['mounts']), "独占外部挂载点应正常保留"
    assert stats['external_available_mounts'] == 1, \
        f"可用计数应排除同名项，实际={stats['external_available_mounts']}"


def test_statistics_all_external_when_no_local(monkeypatch):
    fake = _FakeExternal([_row('EXT-A', 's1'), _row('EXT-B', 's2')])
    monkeypatch.setattr(connection, '_external_manager', lambda: fake)

    cm = ConnectionManager()
    stats = cm.get_statistics()

    assert stats['external_available_mounts'] == 2
    assert sorted(m['mount_name'] for m in stats['mounts']) == ['EXT-A', 'EXT-B']


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-q']))

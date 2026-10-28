#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""挂载点下线时，下游订阅连接计数一致性单元测试

验证修复：remove_mount_connection / add_mount_connection 重连分支会同步清理
下游订阅连接，保证：
  - get_statistics() 的 total_users / total_connections
  - mounts[].user_count（mount_connection_count）
两者在挂载点下线后一致归零，不再出现"顶部在线用户=1，挂载点详情人头=0"的错位。
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.connection import ConnectionManager


_ok = 0
_fail = 0


def check(name, cond, detail=''):
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  [PASS] {name}")
    else:
        _fail += 1
        print(f"  [FAIL] {name}  {detail}")


def main():
    print("[1] 挂载点在线 + 下游订阅后，user_count 应 = 1")
    cm = ConnectionManager()
    cm.add_mount_connection('MOUNT1', '1.1.1.1')
    cm.add_user_connection('alice', 'MOUNT1', '2.2.2.2')

    stats = cm.get_statistics()
    check('total_connections == 1', stats['total_connections'] == 1, repr(stats))
    check('total_users == 1', stats['total_users'] == 1, repr(stats))
    m = [x for x in stats['mounts'] if x['mount_name'] == 'MOUNT1'][0]
    check("mounts[].user_count == 1", m['user_count'] == 1, repr(m))

    print("[2] 挂载点下线后，下游连接应被清理，两侧计数同步归零")
    cm.remove_mount_connection('MOUNT1', '测试下线')
    stats2 = cm.get_statistics()
    check('下线后 total_connections == 0', stats2['total_connections'] == 0, repr(stats2))
    check('下线后 total_users == 0', stats2['total_users'] == 0, repr(stats2))
    check('下线后 online_users 为空', len(cm.online_users) == 0, repr(dict(cm.online_users)))
    check('下线后 mount_connection_count 归零',
          cm.mount_connection_count.get('MOUNT1', 0) == 0,
          repr(dict(cm.mount_connection_count)))

    print("[3] 多个下游 + 多个挂载点：只清理目标挂载点的下游")
    cm3 = ConnectionManager()
    cm3.add_mount_connection('A', '1.1.1.1')
    cm3.add_mount_connection('B', '1.1.1.2')
    cm3.add_user_connection('alice', 'A', '2.2.2.2')
    cm3.add_user_connection('bob', 'A', '2.2.2.3')
    cm3.add_user_connection('carol', 'B', '2.2.2.4')

    s = cm3.get_statistics()
    check('三连接全在线', s['total_connections'] == 3, repr(s))

    cm3.remove_mount_connection('A', '下线 A')
    s = cm3.get_statistics()
    check('A 下线后只剩 B 的 1 条连接', s['total_connections'] == 1, repr(s))
    check('carol 仍在', s['total_users'] == 1, repr(s))
    a = [x for x in s['mounts'] if x['mount_name'] == 'A']
    b = [x for x in s['mounts'] if x['mount_name'] == 'B']
    check('A 的 user_count == 0', len(a) == 0 or a[0]['user_count'] == 0, repr(a))
    check('B 的 user_count == 1', b[0]['user_count'] == 1, repr(b))

    print("[4] 相同 IP 重连分支也应清理旧下游")
    cm4 = ConnectionManager()
    cm4.add_mount_connection('M', '1.1.1.1')
    cm4.add_user_connection('alice', 'M', '2.2.2.2')
    # 模拟相同 IP 重复连接（不经过 remove_mount_connection）
    cm4.add_mount_connection('M', '1.1.1.1')
    s = cm4.get_statistics()
    check('重连后旧下游已清理，total_connections == 0', s['total_connections'] == 0, repr(s))
    check('重连后 total_users == 0', s['total_users'] == 0, repr(s))

    print(f"\n结果: {_ok} 通过 / {_fail} 失败")
    sys.exit(1 if _fail else 0)


if __name__ == '__main__':
    main()

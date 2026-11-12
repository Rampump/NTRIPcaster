#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
demo_reset.py - 演示站数据清理工具

演示站对公网开放「新增用户 / 新增挂载点」，不清理的话半年后就是垃圾场。
本脚本按创建时间删除过期的用户与挂载点，默认保留 60 天。

用法：
    python3 scripts/demo_reset.py                  # 预览（只统计，不删）
    python3 scripts/demo_reset.py --apply          # 真删
    python3 scripts/demo_reset.py --days 30 --apply
    python3 scripts/demo_reset.py --apply --include-no-date
    python3 scripts/demo_reset.py --db /path/to/2rtk.db

行为说明：
    1. 只处理 users / mounts 两张表，永远不动 admins（管理员口令是演示入口）；
    2. created_at 为空的行（老库升级过来的历史数据）默认**保留**，
       需要一并清理时显式加 --include-no-date；
    3. 删用户前先把 mounts.user_id 置空（外键是 ON DELETE SET NULL，
       但 SQLite 默认不开启外键约束，所以这里显式处理）；
    4. 删除前会打印清单，--apply 才真正提交。

部署建议（systemd timer，每天跑一次）：
    [Unit]
    Description=NTRIP caster demo cleanup

    [Service]
    Type=oneshot
    WorkingDirectory=/opt/2rtk
    ExecStart=/opt/2rtk/venv/bin/python scripts/demo_reset.py --apply --days 60

    [Timer]
    OnCalendar=daily
    Persistent=true

    [Install]
    WantedBy=timers.target
"""

import argparse
import os
import sqlite3
import sys
import time


def resolve_db_path(explicit=None):
    """定位数据库文件：命令行参数 > 环境变量 DATABASE_PATH > config.ini"""
    if explicit:
        return explicit
    if os.environ.get('DATABASE_PATH'):
        return os.environ['DATABASE_PATH']

    # 脚本可能在任意目录下被调用，先按项目根目录找 config.ini
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    try:
        from src import config
        path = config.DATABASE_PATH
        if not os.path.isabs(path):
            path = os.path.join(root, path)
        return path
    except Exception as e:
        raise SystemExit(f"无法从 config.ini 读取数据库路径: {e}\n请用 --db 指定数据库文件")


def table_columns(conn, table):
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def collect_expired(conn, table, cutoff, include_no_date):
    """返回待删除的行：[{'id':.., 'name':.., 'created_at':..}]"""
    cols = table_columns(conn, table)
    name_col = 'username' if table == 'users' else 'mount'
    if 'created_at' not in cols:
        return []

    if include_no_date:
        sql = f"SELECT id, {name_col}, created_at FROM {table} WHERE created_at IS NULL OR created_at < ?"
    else:
        sql = f"SELECT id, {name_col}, created_at FROM {table} WHERE created_at IS NOT NULL AND created_at < ?"
    cur = conn.execute(sql, (cutoff,))
    return [{'id': r[0], 'name': r[1], 'created_at': r[2]} for r in cur.fetchall()]


def fmt_ts(ts):
    if not ts:
        return '未知'
    try:
        return time.strftime('%Y-%m-%d %H:%M', time.localtime(float(ts)))
    except (ValueError, OSError):
        return str(ts)


def main():
    parser = argparse.ArgumentParser(description='演示站数据清理：删除超过指定天数的用户与挂载点')
    parser.add_argument('--db', help='SQLite 数据库文件路径（默认取 config.ini）')
    parser.add_argument('--days', type=int, default=60, help='保留天数，默认 60')
    parser.add_argument('--apply', action='store_true', help='真正执行删除（不加则只预览）')
    parser.add_argument('--include-no-date', action='store_true',
                        help='连 created_at 为空的历史行一起清理（默认保留）')
    args = parser.parse_args()

    if args.days <= 0:
        raise SystemExit('--days 必须大于 0')

    db_path = resolve_db_path(args.db)
    if not os.path.exists(db_path):
        raise SystemExit(f"数据库不存在: {db_path}")

    cutoff = time.time() - args.days * 86400
    print(f"数据库: {db_path}")
    print(f"保留最近 {args.days} 天（早于 {fmt_ts(cutoff)} 的数据视为过期）")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA busy_timeout=10000")
    try:
        users = collect_expired(conn, 'users', cutoff, args.include_no_date)
        mounts = collect_expired(conn, 'mounts', cutoff, args.include_no_date)

        print(f"\n待删除用户 {len(users)} 个：")
        for u in users:
            print(f"  - {u['name']}  (创建于 {fmt_ts(u['created_at'])})")
        print(f"待删除挂载点 {len(mounts)} 个：")
        for m in mounts:
            print(f"  - {m['name']}  (创建于 {fmt_ts(m['created_at'])})")

        if not users and not mounts:
            print("\n没有需要清理的数据。")
            return 0

        if not args.apply:
            print("\n这是预览模式，未做任何修改。确认无误后加 --apply 执行。")
            return 0

        with conn:  # 事务：要么全删，要么不动
            # 先解绑挂载点归属，避免删用户后留下悬空的 user_id
            if users:
                ids = [u['id'] for u in users]
                placeholders = ','.join('?' * len(ids))
                conn.execute(
                    f"UPDATE mounts SET user_id = NULL WHERE user_id IN ({placeholders})", ids)
            for table, rows in (('mounts', mounts), ('users', users)):
                for row in rows:
                    conn.execute(f"DELETE FROM {table} WHERE id = ?", (row['id'],))

        # 删除后回收空间（WAL 下不会立刻缩小文件，但能保证一致性）
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.DatabaseError:
            pass

        print(f"\n已删除：用户 {len(users)} 个，挂载点 {len(mounts)} 个。")
        return 0
    finally:
        conn.close()


if __name__ == '__main__':
    sys.exit(main())

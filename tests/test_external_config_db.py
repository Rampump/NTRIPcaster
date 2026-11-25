#!/usr/bin/env python3
"""外部 Caster 配置/数据库测试：三态开关、幂等迁移、CRUD、账号池互斥。

运行：python tests/test_external_config_db.py
"""
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ['DATABASE_PATH'] = os.path.join(tempfile.mkdtemp(), 'cfg.db')
os.environ['EXTERNAL_CASTER_ENABLED'] = 'false'   # 显式 false 必须压过 ini
os.environ['EXTERNAL_UI_ENABLED'] = 'true'
os.environ['EXTERNAL_CASTER_UI_ENABLED'] = 'true'
os.environ['EXTERNAL_STR_REFRESH'] = '100'

from src import config, database  # noqa: E402

FAILS = []


def check(name, actual, expected):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'} {name}: {actual!r} (expected {expected!r})")
    if not ok:
        FAILS.append(name)


def main():
    print('[1] 三态开关')
    check('显式 false 覆盖 ini', config.EXTERNAL_CASTER_ENABLED, False)
    check('显式 true', config.EXTERNAL_CASTER_UI_ENABLED, True)
    check('整数覆盖', config.EXTERNAL_STR_REFRESH, 100)
    check('默认 6h', config.EXTERNAL_STR_BACKOFF_MAX, 86400)
    check('密钥持久化标记', config.SECRET_KEY_PERSISTENT, True)

    print('[2] 重复初始化不报 duplicate column')
    database.init_db()
    database.init_db()
    with database._connect() as conn:
        cols = {r[1] for r in conn.execute('PRAGMA table_info(mounts)').fetchall()}
        version = conn.execute('PRAGMA user_version').fetchone()[0]
    check('source_type 列', 'source_type' in cols, True)
    check('schema v2', version, 2)

    print('[3] CRUD 与多源融合去重（外部挂载点不写本地 mounts）')
    ok, s1 = database.create_external_caster({'name': 'A', 'host': '1.1.1.1', 'port': 2101})
    ok2, s2 = database.create_external_caster({'name': 'B', 'host': '2.2.2.2', 'port': 2102})
    check('两个源创建', (ok, ok2), (True, True))
    check('重名拒绝', database.create_external_caster(
        {'name': 'A', 'host': 'x', 'port': 1})[0], False)
    line = 'STR;M1;X;RTCM 3;1;0;0;N;CHN;1;2;0;0;g;N;B;N;9600;'
    database.replace_external_mounts(s1, [('M1', line, 1.0, 2.0)])
    database.replace_external_mounts(s2, [('M1', line, 3.0, 4.0), ('M2', line, 5.0, 6.0)])
    check('外部挂载点不进入本地 mounts',
          len([m for m in database.get_all_mounts() if m[1] in ('M1', 'M2')]), 0)
    check('外部可用判定', database.is_external_mount('M1'), True)
    check('本地行未受影响', database.is_external_mount('NOPE'), False)

    print('[4] 账号池互斥与 holder 校验')
    database.create_external_account({'source_id': s1, 'username': 'u1'})
    database.create_external_account({'source_id': s1, 'username': 'u2'})
    a1 = database.take_account(s1, 'web:c1', ttl=60)
    a2 = database.take_account(s1, 'web:c2', ttl=60)
    check('两账号互不冲突', (a1['username'], a2['username']), ('u1', 'u2'))
    check('第三个取号失败', database.take_account(s1, 'web:c3', ttl=60), None)
    check('错误 holder 不能释放', database.release_account(a1['id'], 'web:wrong'), False)
    check('正确 holder 释放', database.release_account(a1['id'], 'web:c1'), True)
    check('释放后可再取', bool(database.take_account(s1, 'web:c3', ttl=60)), True)

    print('[5] profile 播种幂等')
    n1 = database.seed_agent_profiles([
        {'agent_string': 'NTRIP RTKLIB/2.4.2', 'vendor': 'RTKLIB', 'weight': 12},
        {'agent_string': 'Mozilla/5.0', 'is_browser': 1, 'weight': 1}])
    n2 = database.seed_agent_profiles([
        {'agent_string': 'NTRIP RTKLIB/2.4.2', 'vendor': 'RTKLIB', 'weight': 12}])
    check('首次插入 2 条', n1, 2)
    check('重复播种不新增', n2, 0)
    check('浏览器类被过滤', len(database.list_agent_profiles(True)), 1)

    if FAILS:
        print(f'失败 {len(FAILS)} 项: {FAILS}')
        sys.exit(1)
    print('全部通过')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""config.ini 增量迁移脚本（scripts/merge_config.py）的行为测试

背景：升级时 update.sh / 容器 entrypoint 都不覆盖 config.ini（那是用户的部署配置），
但新版本新增的键在老文件里根本不存在 —— 于是「代码有 demo_mode，配置里查无此项」。
本测试锁死迁移脚本的这几条底线：

  1. 缺失的键/段会被补上，值取自 config.ini.example
  2. 用户改过的值一个都不动
  3. 原有注释、段顺序、文件行尾（CRLF/LF）原样保留
  4. 新键插在所属段内，不会跑到别的段里
  5. 幂等：重复执行不产生重复键（重复键会让 configparser 直接抛错）
  6. dry-run 不写文件；--apply 写前备份
  7. 配置文件本身有重复键时，报错退出且不碰文件

用法：
    python tests/test_merge_config.py
    python tests/test_merge_config.py -v
"""

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(_ROOT, 'scripts', 'merge_config.py')

VERBOSE = False
PASS = 0
FAIL = []


def check(name, got, want):
    global PASS
    if got == want:
        PASS += 1
        if VERBOSE:
            print(f"  [PASS] {name}")
        return True
    FAIL.append(f"{name}: got={got!r} want={want!r}")
    print(f"  [FAIL] {name}: got={got!r} want={want!r}")
    return False


def check_true(name, cond, detail=''):
    global PASS
    if cond:
        PASS += 1
        if VERBOSE:
            print(f"  [PASS] {name}")
        return True
    FAIL.append(f"{name}: {detail}")
    print(f"  [FAIL] {name} {detail}")
    return False


def load_module():
    spec = importlib.util.spec_from_file_location('ntrip_merge_config', SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


EXAMPLE = """\
# 示例文件头部说明

[app]
name = demo
version = 9.9.9

[web]
port = 5757
# 解析线程上限
max_web_parsers = 4
public_access = false

[security]
demo_mode = false

[payment]
# 收款码，留空则不显示
alipay_qr_code =
"""

OLD_CONFIG = """\
[app]
# 应用名
name = 用户改过的名字
version = 2.0.0

[web]
# Web服务
port = 9999
page_refresh_interval = 30

[database]
path = /data/rtk.db
"""


def write(path, text, crlf=False):
    data = text.replace('\n', '\r\n') if crlf else text
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(data)


def read(path):
    with open(path, 'r', encoding='utf-8', newline='') as f:
        return f.read()


def run_cli(*args):
    return subprocess.run([sys.executable, SCRIPT] + list(args),
                          capture_output=True, text=True, encoding='utf-8')


def case_basic(tmp):
    print("[1] 补全新键 + 不动用户值 + 保留注释")
    cfg = os.path.join(tmp, 'config.ini')
    ex = os.path.join(tmp, 'config.ini.example')
    write(cfg, OLD_CONFIG)
    write(ex, EXAMPLE)

    r = run_cli('--config', cfg, '--example', ex, '--apply')
    check('退出码', r.returncode, 0)
    text = read(cfg)

    check_true('补上 max_web_parsers', 'max_web_parsers = 4' in text)
    check_true('补上 public_access', 'public_access = false' in text)
    check_true('补上 demo_mode', 'demo_mode = false' in text)
    check_true('用户改过的 name 未被覆盖', 'name = 用户改过的名字' in text)
    check_true('用户改过的 port 未被覆盖', 'port = 9999' in text)
    check_true('示例里的值没有写进来覆盖 port', 'port = 5757' not in text)
    check_true('原有注释保留', '# Web服务' in text)
    check_true('示例注释随新键写入', '# 解析线程上限' in text)
    check_true('段顺序按示例补在尾部', text.index('[payment]') > text.index('[database]'))

    # 新键必须在 [web] 段内，而不是被追加到文件末尾的 [database] 后面
    web_start = text.index('[web]')
    db_start = text.index('[database]')
    check_true('max_web_parsers 落在 [web] 段内',
               web_start < text.index('max_web_parsers') < db_start)

    import configparser
    cp = configparser.ConfigParser()
    cp.read_string(text)
    check('解析后 name 值', cp.get('app', 'name'), '用户改过的名字')
    check('解析后 port 值', cp.get('web', 'port'), '9999')
    check('解析后 public_access 值', cp.getboolean('web', 'public_access'), False)
    check('解析后 demo_mode 值', cp.getboolean('security', 'demo_mode'), False)
    check('空值键不产生尾随空格', 'alipay_qr_code = ' not in text, True)

    # 备份
    baks = [f for f in os.listdir(tmp) if f.startswith('config.ini.bak.')]
    check('生成备份文件', len(baks), 1)
    if baks:
        check('备份内容等于原文件', read(os.path.join(tmp, baks[0])), OLD_CONFIG)


def case_idempotent(tmp):
    print("[2] 幂等：再跑一次没有新增")
    cfg = os.path.join(tmp, 'c2.ini')
    ex = os.path.join(tmp, 'c2.example')
    write(cfg, OLD_CONFIG)
    write(ex, EXAMPLE)
    run_cli('--config', cfg, '--example', ex, '--apply')
    first = read(cfg)

    r = run_cli('--config', cfg, '--example', ex, '--apply')
    check('退出码', r.returncode, 0)
    check('第二次未新增', 'unchanged' in r.stdout or '已是最新' in r.stdout, True)
    check('文件内容完全不变', read(cfg), first)
    check_true('没有重复键', first.count('demo_mode') == 1 and first.count('public_access') == 1)


def case_dry_run(tmp):
    print("[3] dry-run 不写文件")
    cfg = os.path.join(tmp, 'c3.ini')
    ex = os.path.join(tmp, 'c3.example')
    write(cfg, OLD_CONFIG)
    write(ex, EXAMPLE)
    r = run_cli('--config', cfg, '--example', ex)
    check('退出码', r.returncode, 0)
    check_true('预览里列出了新增项', 'demo_mode' in r.stdout)
    check_true('提示需要 --apply', '--apply' in r.stdout)
    check('文件未被修改', read(cfg), OLD_CONFIG)
    check('未生成备份', len([f for f in os.listdir(tmp) if f.startswith('c3.ini.bak')]), 0)


def case_crlf(tmp):
    print("[4] 保留 CRLF 行尾")
    cfg = os.path.join(tmp, 'c4.ini')
    ex = os.path.join(tmp, 'c4.example')
    write(cfg, OLD_CONFIG, crlf=True)
    write(ex, EXAMPLE, crlf=True)
    run_cli('--config', cfg, '--example', ex, '--apply')
    raw = open(cfg, 'rb').read()
    check_true('仍是 CRLF', raw.count(b'\r\n') > 0)
    check('没有裸 LF', raw.count(b'\n') - raw.count(b'\r\n'), 0)


def case_duplicate_key(tmp):
    print("[5] 目标文件有重复键 → 报错且不修改")
    cfg = os.path.join(tmp, 'c5.ini')
    ex = os.path.join(tmp, 'c5.example')
    broken = '[security]\ndemo_mode = true\ndemo_mode = false\n'
    write(cfg, broken)
    write(ex, EXAMPLE)
    r = run_cli('--config', cfg, '--example', ex, '--apply')
    check('退出码', r.returncode, 2)
    check_true('提示重复键', '重复' in (r.stderr or '') or 'Duplicate' in (r.stderr or ''))
    check('文件未被修改', read(cfg), broken)


def case_missing_file(tmp):
    print("[6] 目标/示例不存在 → 非零退出")
    ex = os.path.join(tmp, 'c6.example')
    write(ex, EXAMPLE)
    r = run_cli('--config', os.path.join(tmp, 'nope.ini'), '--example', ex)
    check('目标不存在退出码', r.returncode, 2)
    r2 = run_cli('--config', ex, '--example', os.path.join(tmp, 'nope.example'))
    check('示例不存在退出码', r2.returncode, 2)


def case_real_example(tmp):
    print("[7] 用仓库里真实的 config.ini.example 跑一遍老配置")
    mod = load_module()
    old = ('[app]\nname = old\n\n[web]\nport = 5757\n\n[security]\nsecret_key =\n')
    cfg = os.path.join(tmp, 'real.ini')
    write(cfg, old)
    r = run_cli('--config', cfg,
                '--example', os.path.join(_ROOT, 'config.ini.example'), '--apply')
    check('退出码', r.returncode, 0)
    text = read(cfg)
    for key in ('demo_mode', 'public_access', 'max_web_parsers', 'parser_client_ttl',
                'parser_ip_rate_per_minute', 'login_lockout_seconds',
                'shutdown_timeout', 'download_enforce_user_binding'):
        check_true(f'真实示例里的 {key} 已补上', key in text)

    import configparser
    cp = configparser.ConfigParser()
    cp.read_string(text)
    check('原有值未变', cp.get('app', 'name'), 'old')
    check('示例段被补齐', 'payment' in cp.sections(), True)

    # 再跑一次必须幂等（真实示例里有大量注释，容易在注释处理上翻车）
    before = read(cfg)
    r2 = run_cli('--config', cfg,
                 '--example', os.path.join(_ROOT, 'config.ini.example'), '--apply')
    check('二次执行退出码', r2.returncode, 0)
    check('二次执行无变更', read(cfg), before)
    check_true('脚本模块可导入', hasattr(mod, 'build_inserts'))


def main():
    global VERBOSE
    ap = argparse.ArgumentParser()
    ap.add_argument('-v', '--verbose', action='store_true')
    args = ap.parse_args()
    VERBOSE = args.verbose

    if not os.path.exists(SCRIPT):
        print(f"找不到 {SCRIPT}")
        return 1

    tmp = tempfile.mkdtemp(prefix='merge_cfg_test_')
    try:
        case_basic(tmp)
        case_idempotent(tmp)
        case_dry_run(tmp)
        case_crlf(tmp)
        case_duplicate_key(tmp)
        case_missing_file(tmp)
        case_real_example(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAIL:
        print(f"结果: {PASS} 通过 / {len(FAIL)} 失败")
        for f in FAIL:
            print("  !", f)
        return 1
    print(f"结果: {PASS} 通过 / 0 失败")
    return 0


if __name__ == '__main__':
    sys.exit(main())
